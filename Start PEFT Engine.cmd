@echo off
title OmniGrapher PEFT Engine
echo Starting OmniGrapher PEFT Engine (Multi-LoRA Adapter Server)...
echo.
echo Base Model: Qwen2.5-3B-Instruct (4-bit quantized)
echo Adapters:   security, summarizer, reasoner, indexer, orchestrator
echo Port:       8003
echo.

cd /d "D:\Upwork\ai_knowledge_base_suite\peft_engine"
.venv\Scripts\python.exe -m uvicorn peft_engine.main:app --host 0.0.0.0 --port 8003

pause
