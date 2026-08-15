import chromadb

# 1. Ensuring we connect to the correct path
client = chromadb.PersistentClient(path="./chroma_db")

try:
    # 2. Getting the collection
    collection = client.get_collection(name="knowledge_base")
    
    # 3. Fetching the first 5 or 10 records (including metadata and IDs)
    results = collection.peek(limit=5)
    
    print("=" * 60)
    print(f" COLLECTION: knowledge_base (Total Items: {collection.count()})")
    print("=" * 60)
    
    if results and results["ids"]:
        for i in range(len(results["ids"])):
            item_id = results["ids"][i]
            metadata = results["metadatas"][i] if results["metadatas"] else {}
            document = results["documents"][i] if results["documents"] else ""
            
            print(f"\n[Record #{i+1}]")
            print(f" 🔹 ID: {item_id}")
            print(f" 🔹 Metadata: {metadata}")
            print(f" 🔹 Snippet: {document[:200]}...") # প্রথম ২০০ ক্যারেক্টার দেখাবে
            print("-" * 50)
    else:
        print("No records found inside the collection.")
        
except Exception as e:
    print(f"Error accessing collection: {e}")

print("=" * 60)