"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { listDocuments, uploadDocument, DocumentListItem } from "@/lib/api";

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const fetchDocs = useCallback(async () => {
    try {
      const docs = await listDocuments();
      setDocuments(docs);
    } catch {
      // Silently ignore listing failures (backend may be starting up)
    }
  }, []);

  useEffect(() => {
    fetchDocs();
  }, [fetchDocs]);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setError(null);
    setSuccess(null);
    try {
      const result = await uploadDocument(file);
      setSuccess(`"${result.filename}" indexed — ${result.chunk_count} chunks.`);
      await fetchDocs();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-800">Documents</h1>
        <p className="text-sm text-gray-500 mt-1">
          Upload PDF, DOCX, or TXT files to add them to your knowledge base.
        </p>
      </div>

      {/* Upload card */}
      <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
        <h2 className="font-semibold text-gray-700 mb-3">Upload a Document</h2>
        <label className="flex flex-col items-center justify-center w-full h-32 border-2 border-dashed border-indigo-300 rounded-lg cursor-pointer hover:bg-indigo-50 transition-colors">
          <span className="text-sm text-indigo-500 font-medium">
            {uploading ? "Uploading…" : "Click to select file"}
          </span>
          <span className="text-xs text-gray-400 mt-1">PDF, DOCX, TXT, MD</span>
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.docx,.txt,.md"
            className="hidden"
            onChange={handleUpload}
            disabled={uploading}
          />
        </label>
        {success && (
          <p className="mt-3 text-sm text-green-600 font-medium">{success}</p>
        )}
        {error && (
          <p className="mt-3 text-sm text-red-600 font-medium">{error}</p>
        )}
      </div>

      {/* Document list */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100">
          <h2 className="font-semibold text-gray-700">Indexed Documents</h2>
        </div>
        {documents.length === 0 ? (
          <p className="px-6 py-8 text-sm text-gray-400 text-center">
            No documents indexed yet. Upload one above.
          </p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {documents.map((doc) => (
              <li key={doc.id} className="px-6 py-3 flex items-center justify-between">
                <span className="text-sm font-medium text-gray-700">{doc.filename}</span>
                <span className="text-xs text-gray-400">{doc.chunk_count} chunks</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
