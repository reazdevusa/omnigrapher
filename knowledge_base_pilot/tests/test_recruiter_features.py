"""Recruiter-facing capability tests: speech transcription, media URL
ingestion, computer vision, and custom neural-net adapter control.

Heavy third-party engines (faster-whisper, yt-dlp, OCR, PEFT server) are
mocked so the suite runs anywhere without model downloads or network access.
"""

import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.database import ShowcaseResult, create_db_session
from app.main import app
from app.services import (
    media_ingestion_service as media_svc,
    ml_engine_service as ml_svc,
    transcription_service as ts_svc,
    vision_service as vs_svc,
)


# ---------------------------------------------------------------------------
# Transcription service (faster-whisper mocked)
# ---------------------------------------------------------------------------

class _FakeWhisperSegment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class _FakeWhisperInfo:
    language = "en"
    duration = 9.5


class _FakeWhisperModel:
    def __init__(self, *a, **k):
        pass

    def transcribe(self, path, language=None, beam_size=5, vad_filter=True):
        segments = [
            _FakeWhisperSegment(0.0, 2.5, " Hello and welcome to the demo."),
            _FakeWhisperSegment(2.5, 6.0, " Today we cover RAG systems."),
            _FakeWhisperSegment(6.0, 9.5, " Let's dive into adapters."),
        ]
        return iter(segments), _FakeWhisperInfo()


class TestTranscriptionService(unittest.TestCase):
    def setUp(self):
        ts_svc.reset_whisper_model()

    def tearDown(self):
        ts_svc.reset_whisper_model()

    def _patch_whisper(self):
        fake_pkg = types.ModuleType("faster_whisper")
        fake_pkg.WhisperModel = _FakeWhisperModel
        return mock.patch.dict(sys.modules, {"faster_whisper": fake_pkg})

    def test_transcribe_produces_timestamped_json(self):
        with self._patch_whisper():
            result = ts_svc.transcribe_audio("dummy.wav")
            payload = ts_svc.transcript_to_json(result)

        self.assertEqual(payload["language"], "en")
        self.assertEqual(payload["duration_seconds"], 9.5)
        self.assertEqual(len(payload["segments"]), 3)
        self.assertEqual(payload["segments"][0]["start"], 0.0)
        self.assertEqual(payload["segments"][0]["start_label"], "00:00")
        self.assertIn("welcome", payload["text"])

    def test_missing_faster_whisper_raises_runtime_error(self):
        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            with self.assertRaises(RuntimeError):
                ts_svc.get_whisper_model()

    def test_model_is_cached(self):
        with self._patch_whisper():
            m1 = ts_svc.get_whisper_model()
            m2 = ts_svc.get_whisper_model()
            self.assertIs(m1, m2)

    def test_timestamp_formatting(self):
        self.assertEqual(ts_svc.format_timestamp(0), "00:00")
        self.assertEqual(ts_svc.format_timestamp(4 * 60 + 15), "04:15")
        self.assertEqual(ts_svc.format_timestamp(3600 + 61), "1:01:01")
        self.assertEqual(ts_svc.format_timestamp_ms(245.7), "04:05.700")


class TestMediaIngestion(unittest.TestCase):
    def test_is_media_file(self):
        self.assertTrue(media_svc.is_media_file("meeting.MP4"))
        self.assertTrue(media_svc.is_media_file("note.m4a"))
        self.assertFalse(media_svc.is_media_file("doc.pdf"))

    def test_resolve_local_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            media_svc.resolve_media_source(r"C:\no\such\file.mp3")

    def test_resolve_rejects_unsupported_extension(self):
        import tempfile
        from pathlib import Path
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            p = f.name
        try:
            with self.assertRaises(ValueError):
                media_svc.resolve_media_source(p)
        finally:
            Path(p).unlink(missing_ok=True)

    def test_yt_dlp_audio_extraction_bestaudio(self):
        captured = {}

        class _FakeYDL:
            def __init__(self, opts):
                captured["opts"] = opts

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=True):
                captured["url"] = url
                return {"title": "Demo Talk", "id": "abc123", "ext": "mp4"}

        fake_mod = types.ModuleType("yt_dlp")
        fake_mod.YoutubeDL = _FakeYDL

        # Produce the mp3 the post-processor would have created.
        media_svc._MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        produced = media_svc._MEDIA_DIR / "Demo Talk-abc123.mp3"
        produced.write_bytes(b"ID3-fake")

        try:
            with mock.patch.dict(sys.modules, {"yt_dlp": fake_mod}):
                src = media_svc.resolve_media_source("https://youtube.com/watch?v=abc123")

            self.assertEqual(src.source_type, "url")
            self.assertEqual(src.local_path, produced)
            # bestaudio-only format keeps bandwidth down (no hi-res video pull).
            self.assertEqual(captured["opts"]["format"], "bestaudio/best")
            self.assertTrue(captured["opts"]["noplaylist"])
            self.assertEqual(captured["url"], "https://youtube.com/watch?v=abc123")
        finally:
            produced.unlink(missing_ok=True)

    def test_index_transcript_chunks_uses_timestamp_citations(self):
        calls = {}

        class _FakeCollection:
            def upsert(self, ids, documents, metadatas, embeddings):
                calls["ids"] = ids
                calls["documents"] = documents
                calls["metadatas"] = metadatas

        class _FakeEmbed:
            def get_text_embedding(self, text):
                return [0.1, 0.2]

        fake_rag = SimpleNamespace(
            _get_knowledge_base_collection=lambda: _FakeCollection(),
            _get_embed_model=lambda: _FakeEmbed(),
        )

        segments = [
            {"start": 0.0, "end": 5.0, "text": "First topic"},
            {"start": 255.0, "end": 260.0, "text": "Later topic"},
        ]
        with mock.patch.dict(sys.modules, {"app.rag_engine": fake_rag}):
            n = media_svc.index_transcript_chunks(
                filename="meeting.mp4", owner_id=7, segments=segments, source_ref="meeting.mp4"
            )

        self.assertEqual(n, 2)
        self.assertIn("[Source: meeting.mp4 @ 00:00]", calls["documents"][0])
        self.assertIn("[Source: meeting.mp4 @ 04:15]", calls["documents"][1])
        self.assertEqual(calls["metadatas"][1]["start_label"], "04:15")
        self.assertEqual(calls["metadatas"][0]["owner_id"], 7)
        self.assertEqual(calls["metadatas"][0]["media_type"], "audio_transcript")


# ---------------------------------------------------------------------------
# Vision service (OpenCV real, OCR mocked)
# ---------------------------------------------------------------------------

class TestVisionService(unittest.TestCase):
    def test_extension_guards(self):
        self.assertTrue(vs_svc.is_image_file("scan.PNG"))
        self.assertFalse(vs_svc.is_image_file("clip.mp4"))
        self.assertTrue(vs_svc.is_video_file("clip.mkv"))
        self.assertFalse(vs_svc.is_video_file("scan.png"))

    def test_chart_row_extraction_heuristic(self):
        lines = [
            "Revenue 125.5",
            "Growth: 42%",
            "The quick brown fox",
            "Accuracy    0.97",
        ]
        rows = vs_svc._extract_chart_rows(lines)
        self.assertIn(["Revenue", "125.5"], rows)
        self.assertIn(["Growth", "42%"], rows)
        self.assertIn(["Accuracy", "0.97"], rows)
        self.assertNotIn(["The quick brown fox"], [r[0] for r in rows])

    def test_analyze_image_with_fake_ocr(self):
        """Real cv2 decode + fake OCR engine -> merged payload."""
        import tempfile
        from pathlib import Path

        import numpy as np
        cv2 = __import__("cv2")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img_path = f.name
        try:
            img = np.zeros((200, 300, 3), dtype=np.uint8)
            cv2.rectangle(img, (50, 50), (150, 150), (0, 255, 0), -1)
            cv2.imwrite(img_path, img)

            fake_engine = SimpleNamespace(
                return_value=None,
            )

            class _Engine:
                def __call__(self, p):
                    box = [[10, 10], [100, 10], [100, 30], [10, 30]]
                    return [(box, "Total 42", 0.98)], 0.1

            with mock.patch.object(vs_svc, "get_ocr_engine", return_value=_Engine()):
                result = vs_svc.analyze_image(img_path)
                payload = vs_svc.vision_result_to_json(result)

            self.assertEqual(payload["width"], 300)
            self.assertEqual(payload["height"], 200)
            self.assertEqual(payload["ocr_text"], "Total 42")
            self.assertEqual(payload["ocr_blocks"][0]["bbox"], {"x": 10, "y": 10, "w": 90, "h": 20})
            # the green rectangle should show up as a detected region
            self.assertTrue(len(payload["objects"]) >= 1)
        finally:
            Path(img_path).unlink(missing_ok=True)

    def test_analyze_bad_image_raises(self):
        import tempfile
        from pathlib import Path
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"not-an-image")
            p = f.name
        try:
            with self.assertRaises(ValueError):
                vs_svc.analyze_image(p)
        finally:
            Path(p).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# ML engine service (adapter control + telemetry)
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeHttpClient:
    def __init__(self, payload=None):
        self.payload = payload or {"adapters": [{"name": "reasoner"}]}
        self.requested = []

    def get(self, url, timeout=None):
        self.requested.append(url)
        return _FakeResponse(200, self.payload)


class TestMLEngineService(unittest.TestCase):
    def setUp(self):
        ml_svc.reset_ml_engine_service()

    def tearDown(self):
        ml_svc.reset_ml_engine_service()

    def _service(self, payload=None):
        svc = ml_svc.MLEngineService.__new__(ml_svc.MLEngineService)
        from app.services.llm_service import LLMService
        svc.llm = LLMService(peft_engine_url="http://peft.test:8002", adapter_map={"security": "security-guard"})
        svc.llm._http_client = _FakeHttpClient(payload)
        svc._active_adapter = None
        svc._switch_log = []
        return svc

    def test_list_adapters_reports_mapping_and_engine(self):
        svc = self._service()
        out = svc.list_adapters()
        self.assertTrue(out["engine_reachable"])
        self.assertEqual(out["domains"]["security"], "security-guard")
        self.assertIn("reasoner", out["engine_adapters"])

    def test_list_adapters_graceful_when_engine_down(self):
        svc = self._service()
        svc.llm._http_client = SimpleNamespace(get=mock.Mock(side_effect=ConnectionError("down")))
        out = svc.list_adapters()
        self.assertFalse(out["engine_reachable"])
        self.assertEqual(out["engine_adapters"], [])

    def test_switch_adapter_sub_10ms(self):
        """Hot-swap against warmed engine must be well under 10ms."""
        svc = self._service({"adapters": [{"name": "security-guard"}]})
        for _ in range(5):
            result = svc.switch_adapter("security")
            self.assertTrue(result.switched)
            self.assertTrue(result.warmed)
            self.assertLess(result.switch_ms, 10.0)
            self.assertEqual(result.adapter, "security-guard")
        self.assertEqual(len(svc.get_switch_log()), 5)
        self.assertTrue(all(e["switch_ms"] < 10.0 for e in svc.get_switch_log()))

    def test_switch_marks_cold_when_adapter_not_loaded(self):
        svc = self._service({"adapters": [{"name": "other"}]})
        result = svc.switch_adapter("reasoner")
        self.assertFalse(result.warmed)  # not in engine's loaded list -> cold
        self.assertEqual(result.adapter, "reasoner")

    def test_edge_benchmark_returns_metrics(self):
        svc = self._service()
        bench = svc.simulate_edge_inference(input_size=64, iterations=5)
        self.assertGreater(bench.fps, 0)
        self.assertGreater(bench.latency_ms, 0)
        self.assertIn(bench.backend, ("onnxruntime-cpu", "numpy-simulated"))

    def test_gpu_status_shape(self):
        svc = self._service()
        status = svc.get_gpu_status()
        for key in ("gpu_available", "device_count", "vram_total_mb", "vram_used_mb", "device_name"):
            self.assertIn(key, status)


# ---------------------------------------------------------------------------
# Showcase router (end-to-end shape, engines mocked)
# ---------------------------------------------------------------------------

class TestShowcaseRouter(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.auth_user = SimpleNamespace(id=42, role="admin", api_keys={})
        app.dependency_overrides[get_current_user] = lambda: self.auth_user

    def tearDown(self):
        app.dependency_overrides.clear()
        ml_svc.reset_ml_engine_service()
        vs_svc.reset_ocr_engine()
        ts_svc.reset_whisper_model()

    def test_transcribe_rejects_bad_extension(self):
        resp = self.client.post(
            "/api/showcase/transcribe",
            files={"file": ("essay.txt", b"hello", "text/plain")},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Unsupported media type", resp.text)

    def test_transcribe_upload_happy_path(self):
        fake_pkg = types.ModuleType("faster_whisper")
        fake_pkg.WhisperModel = _FakeWhisperModel

        with mock.patch.dict(sys.modules, {"faster_whisper": fake_pkg}), \
             mock.patch.object(media_svc, "index_transcript_chunks", return_value=3), \
             mock.patch.object(
                 media_svc, "index_transcript_graph",
                 return_value={"status": "indexed", "entities": 5, "relationships": 2},
             ):
            resp = self.client.post(
                "/api/showcase/transcribe",
                files={"file": ("demo.wav", b"RIFF-fake-audio", "audio/wav")},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["language"], "en")
        self.assertEqual(len(data["segments"]), 3)
        self.assertEqual(data["segments"][2]["start_label"], "00:06")
        self.assertEqual(data["indexed_chunks"], 3)
        self.assertEqual(data["graph_entities"], 5)

    def test_transcribe_url_missing_file(self):
        resp = self.client.post(
            "/api/showcase/transcribe-url",
            json={"url": r"C:\missing\clip.mp4", "index": False},
        )
        self.assertEqual(resp.status_code, 400)

    def test_ml_adapters_endpoint(self):
        svc = ml_svc.get_ml_engine_service()
        svc.llm._http_client = _FakeHttpClient({"adapters": [{"name": "indexer"}]})
        resp = self.client.get("/api/showcase/ml/adapters")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["engine_reachable"])
        self.assertIn("indexer", data["engine_adapters"])

    def test_ml_switch_adapter_endpoint(self):
        svc = ml_svc.get_ml_engine_service()
        svc.llm._http_client = _FakeHttpClient({"adapters": [{"name": "summarizer"}]})
        resp = self.client.post("/api/showcase/ml/switch-adapter", json={"adapter": "summarizer"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["switched"])
        self.assertLess(data["switch_ms"], 10.0)

    def test_ml_edge_benchmark_endpoint(self):
        resp = self.client.get("/api/showcase/ml/edge-benchmark?input_size=64&iterations=5")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreater(data["fps"], 0)

    def test_showcase_endpoints_require_auth(self):
        app.dependency_overrides.clear()
        resp = self.client.get("/api/showcase/ml/adapters")
        self.assertIn(resp.status_code, (401, 403))


# ---------------------------------------------------------------------------
# Multimedia Q&A + GraphRAG indexing
# ---------------------------------------------------------------------------

class TestMediaQA(unittest.TestCase):
    def test_query_media_chunks_builds_filtered_query(self):
        captured = {}

        class _FakeCollection:
            def count(self):
                return 10

            def query(self, query_embeddings, n_results, where, include):
                captured["where"] = where
                captured["n_results"] = n_results
                return {
                    "documents": [["[Source: meeting.mp4 @ 04:15] adapters discussed"]],
                    "metadatas": [[{
                        "file_name": "meeting.mp4",
                        "source_ref": "meeting.mp4",
                        "start": 255.0,
                        "end": 260.0,
                        "start_label": "04:15",
                    }]],
                    "distances": [[0.2]],
                }

        class _FakeEmbed:
            def get_text_embedding(self, text):
                return [0.1] * 8

        fake_rag = SimpleNamespace(
            _get_knowledge_base_collection=lambda: _FakeCollection(),
            _get_embed_model=lambda: _FakeEmbed(),
        )
        with mock.patch.dict(sys.modules, {"app.rag_engine": fake_rag}):
            hits = media_svc.query_media_chunks(
                question="what about adapters?",
                owner_id=7,
                source_ref="meeting.mp4",
                top_k=4,
            )

        self.assertEqual(captured["n_results"], 4)
        # owner + media_type + source scoping all present in the where clause
        self.assertIn("$and", captured["where"])
        clauses = captured["where"]["$and"]
        self.assertIn({"owner_id": 7}, clauses)
        self.assertIn({"media_type": "audio_transcript"}, clauses)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["start_label"], "04:15")
        self.assertAlmostEqual(hits[0]["score"], 0.8)

    def test_index_transcript_graph_uses_synthetic_doc_id(self):
        ingested = {}

        def _fake_ingest(doc_id, fname, chunks):
            ingested.update({"doc_id": doc_id, "filename": fname, "chunks": chunks})
            return {"status": "indexed", "entities": 3, "relationships": 1}

        segments = [{"start": 255.0, "end": 260.0, "text": "Later topic"}]
        import app.services.graph_rag as real_graph
        with mock.patch.object(real_graph, "is_available", return_value=True), \
             mock.patch.object(real_graph, "ingest_document_graph", side_effect=_fake_ingest):
            out = media_svc.index_transcript_graph(
                filename="meeting.mp4", owner_id=7,
                segments=segments, source_ref="meeting.mp4",
            )

        self.assertEqual(out["entities"], 3)
        # negative synthetic id — can never collide with real DB document ids
        self.assertLess(ingested["doc_id"], 0)
        self.assertIn("[Source: meeting.mp4 @ 04:15]", ingested["chunks"][0]["text"])

    def test_index_transcript_graph_skipped_when_disabled(self):
        import app.services.graph_rag as real_graph
        with mock.patch.object(real_graph, "is_available", return_value=False):
            out = media_svc.index_transcript_graph(
                filename="m.mp4", owner_id=1,
                segments=[{"start": 0, "end": 1, "text": "x"}],
                source_ref="m.mp4",
            )
        self.assertEqual(out["status"], "skipped")


class TestVisualIndexing(unittest.TestCase):
    def test_index_visual_features_embeds_all_modalities(self):
        calls = {}

        class _FakeCollection:
            def upsert(self, ids, documents, metadatas, embeddings):
                calls["documents"] = documents
                calls["metadatas"] = metadatas

        class _FakeEmbed:
            def get_text_embedding(self, text):
                return [0.5, 0.5]

        fake_rag = SimpleNamespace(
            _get_knowledge_base_collection=lambda: _FakeCollection(),
            _get_embed_model=lambda: _FakeEmbed(),
        )
        result = vs_svc.VisionResult(
            ocr_text="Revenue 42",
            objects=[vs_svc.BoundingBox(x=1, y=2, w=30, h=40, label="region", confidence=0.5)],
            chart_rows=[["Revenue", "42"]],
            caption="A bar chart of revenue.",
        )
        with mock.patch.dict(sys.modules, {"app.rag_engine": fake_rag}):
            n = vs_svc.index_visual_features("chart.png", 9, result, "chart.png")

        self.assertEqual(n, len(calls["documents"]))
        joined = "\n".join(calls["documents"])
        self.assertIn("VLM description", joined)
        self.assertIn("OCR text", joined)
        self.assertIn("Chart data: Revenue = 42", joined)
        self.assertIn("Detected regions", joined)
        self.assertTrue(all(m["media_type"] == "visual" for m in calls["metadatas"]))
        self.assertTrue(all(m["owner_id"] == 9 for m in calls["metadatas"]))

    def test_vlm_caption_disabled_without_model(self):
        with mock.patch.object(vs_svc, "VLM_MODEL", ""):
            self.assertEqual(vs_svc.describe_image_with_vlm("x.png"), "")

    def test_vlm_caption_via_ollama(self):
        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"response": "A diagram showing a pipeline."}

        import tempfile
        from pathlib import Path
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG-fake")
            p = f.name
        try:
            with mock.patch.object(vs_svc, "VLM_MODEL", "llava:test"), \
                 mock.patch("requests.post", return_value=_Resp()) as post:
                caption = vs_svc.describe_image_with_vlm(p)
            self.assertEqual(caption, "A diagram showing a pipeline.")
            payload = post.call_args.kwargs["json"]
            self.assertEqual(payload["model"], "llava:test")
            self.assertEqual(len(payload["images"]), 1)
        finally:
            Path(p).unlink(missing_ok=True)


class TestAskEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.auth_user = SimpleNamespace(id=42, role="admin", api_keys={})
        app.dependency_overrides[get_current_user] = lambda: self.auth_user

    def tearDown(self):
        app.dependency_overrides.clear()

    _HITS = [{
        "text": "[Source: meeting.mp4 @ 04:15] adapters discussed",
        "file_name": "meeting.mp4",
        "source_ref": "meeting.mp4",
        "start": 255.0,
        "end": 260.0,
        "start_label": "04:15",
        "score": 0.9,
    }]

    def test_media_ask_returns_answer_with_citations(self):
        import app.routers.showcase as sc
        with mock.patch.object(media_svc, "query_media_chunks", return_value=self._HITS), \
             mock.patch.object(sc, "_graph_context_passages", return_value=[]), \
             mock.patch.object(sc, "_generate_answer", return_value="Adapters were covered [Source: meeting.mp4 @ 04:15]."):
            resp = self.client.post(
                "/api/showcase/ask",
                json={"question": "what about adapters?", "source_ref": "meeting.mp4"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("adapters", data["answer"].lower())
        self.assertEqual(data["citations"][0]["start_label"], "04:15")

    def test_media_ask_empty_index_returns_guidance(self):
        with mock.patch.object(media_svc, "query_media_chunks", return_value=[]):
            resp = self.client.post("/api/showcase/ask", json={"question": "anything"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Transcribe or analyze", resp.json()["answer"])

    def test_visual_ask_endpoint(self):
        import app.routers.showcase as sc
        hits = [{
            "text": "[Image: chart.png] Chart data: Revenue = 42",
            "file_name": "chart.png",
            "source_ref": "chart.png",
            "start": 0.0, "end": 0.0, "start_label": "", "score": 0.7,
        }]
        with mock.patch.object(media_svc, "query_media_chunks", return_value=hits) as q, \
             mock.patch.object(sc, "_graph_context_passages", return_value=[]), \
             mock.patch.object(sc, "_generate_answer", return_value="Revenue is 42 [Source: chart.png]."):
            resp = self.client.post(
                "/api/showcase/visual-ask",
                json={"question": "what is the revenue?", "source_ref": "chart.png"},
            )
        self.assertEqual(resp.status_code, 200)
        # visual asks must filter on the visual media_type
        self.assertEqual(q.call_args.kwargs["media_type"], "visual")
        self.assertEqual(resp.json()["citations"][0]["file_name"], "chart.png")


# ---------------------------------------------------------------------------
# Showcase result persistence + history endpoints
# ---------------------------------------------------------------------------

class TestShowcaseHistory(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.auth_user = SimpleNamespace(id=42, role="admin", api_keys={})
        app.dependency_overrides[get_current_user] = lambda: self.auth_user
        # deterministic slate
        db = create_db_session()
        try:
            db.query(ShowcaseResult).delete()
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        app.dependency_overrides.clear()
        ml_svc.reset_ml_engine_service()
        ts_svc.reset_whisper_model()

    def _insert(self, owner_id, kind, title, payload=None):
        import json
        db = create_db_session()
        try:
            row = ShowcaseResult(
                owner_id=owner_id,
                kind=kind,
                title=title,
                source_ref=title,
                payload=json.dumps(payload or {}),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
        finally:
            db.close()

    def test_results_persist_across_sessions(self):
        rid = self._insert(42, "transcript", "demo.wav", {"segments": [{"text": "hi"}]})
        # a brand-new session/connection must still see the row (i.e. survives restart)
        db = create_db_session()
        try:
            row = db.query(ShowcaseResult).filter(ShowcaseResult.id == rid).one()
            self.assertEqual(row.title, "demo.wav")
            self.assertEqual(row.owner_id, 42)
        finally:
            db.close()

    def test_history_list_scoped_to_user(self):
        mine = self._insert(42, "transcript", "mine.wav", {"segments": [{"text": "x"}], "duration_seconds": 9.5, "language": "en"})
        self._insert(99, "transcript", "theirs.wav", {"segments": []})
        self._insert(42, "visual", "chart.png", {"ocr_blocks": [{"text": "t"}], "objects": [], "indexed_chunks": 2})
        self._insert(42, "adapter_switch", "summarizer", {"adapter": "summarizer", "switch_ms": 4.2, "warmed": True})

        resp = self.client.get("/api/showcase/history")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertEqual(len(items), 3)
        self.assertNotIn("theirs.wav", [i["title"] for i in items])
        # most recent first + summaries computed
        transcript = next(i for i in items if i["id"] == mine)
        self.assertEqual(transcript["summary"]["segments"], 1)
        visual = next(i for i in items if i["kind"] == "visual")
        self.assertEqual(visual["summary"]["ocr_blocks"], 1)
        switch = next(i for i in items if i["kind"] == "adapter_switch")
        self.assertEqual(switch["summary"]["switch_ms"], 4.2)

    def test_history_list_kind_filter(self):
        self._insert(42, "transcript", "a.wav")
        self._insert(42, "visual", "b.png")
        resp = self.client.get("/api/showcase/history?kind=visual")
        items = resp.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "visual")

    def test_history_detail_and_isolation(self):
        mine = self._insert(42, "transcript", "mine.wav", {"segments": [{"text": "hello"}]})
        theirs = self._insert(99, "transcript", "theirs.wav", {})

        resp = self.client.get(f"/api/showcase/history/{mine}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["payload"]["segments"][0]["text"], "hello")

        # another user's record is invisible, not leaked
        resp = self.client.get(f"/api/showcase/history/{theirs}")
        self.assertEqual(resp.status_code, 404)

    def test_history_delete(self):
        mine = self._insert(42, "visual", "img.png")
        theirs = self._insert(99, "visual", "other.png")

        resp = self.client.delete(f"/api/showcase/history/{theirs}")
        self.assertEqual(resp.status_code, 404)

        resp = self.client.delete(f"/api/showcase/history/{mine}")
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(f"/api/showcase/history/{mine}")
        self.assertEqual(resp.status_code, 404)

    def test_transcribe_persists_result(self):
        fake_pkg = types.ModuleType("faster_whisper")
        fake_pkg.WhisperModel = _FakeWhisperModel

        with mock.patch.dict(sys.modules, {"faster_whisper": fake_pkg}), \
             mock.patch.object(media_svc, "index_transcript_chunks", return_value=3), \
             mock.patch.object(
                 media_svc, "index_transcript_graph",
                 return_value={"status": "indexed", "entities": 5, "relationships": 2},
             ):
            resp = self.client.post(
                "/api/showcase/transcribe",
                files={"file": ("demo.wav", b"RIFF-fake-audio", "audio/wav")},
            )
        self.assertEqual(resp.status_code, 200)

        hist = self.client.get("/api/showcase/history?kind=transcript").json()["items"]
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["title"], "demo.wav")
        self.assertEqual(hist[0]["summary"]["segments"], 3)

        detail = self.client.get(f"/api/showcase/history/{hist[0]['id']}").json()
        self.assertEqual(detail["payload"]["language"], "en")
        self.assertEqual(len(detail["payload"]["segments"]), 3)

    def test_adapter_switch_persists_result(self):
        svc = ml_svc.get_ml_engine_service()
        svc.llm._http_client = _FakeHttpClient({"adapters": [{"name": "summarizer"}]})
        resp = self.client.post("/api/showcase/ml/switch-adapter", json={"adapter": "summarizer"})
        self.assertEqual(resp.status_code, 200)

        hist = self.client.get("/api/showcase/history?kind=adapter_switch").json()["items"]
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["title"], "summarizer")
        self.assertIn("switch_ms", hist[0]["summary"])


if __name__ == "__main__":
    unittest.main()
