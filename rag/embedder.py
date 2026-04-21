# rag/embedder.py
# 使用 text-embedding-3-small (via OpenRouter) 將健康規則向量化，儲存至 ChromaDB

import os
import chromadb
from openai import OpenAI
from rag.health_rules import get_all_rules, get_rag_indexing_text

# ChromaDB 持久化目錄（同個 rag/ 資料夾下）
_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
_COLLECTION_NAME = "health_rules"

# OpenRouter client（Embedding）
_client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY", ""),
    base_url="https://openrouter.ai/api/v1",
)


def _get_collection():
    """取得（或建立）ChromaDB Collection"""
    chroma_client = chromadb.PersistentClient(path=_DB_DIR)
    collection = chroma_client.get_or_create_collection(
        name=_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    return collection


def build_knowledge_base(force_rebuild: bool = False) -> int:
    """
    建立健康規則向量知識庫。
    若知識庫已存在且 force_rebuild=False 則跳過，直接回傳現有數量。
    回傳實際存入的規則數量。
    """
    collection = _get_collection()

    # 若已有資料且不強制重建，直接回傳
    existing_count = collection.count()
    if existing_count > 0 and not force_rebuild:
        print(f"✅ 健康規則知識庫已存在，共 {existing_count} 筆規則，跳過重建。")
        return existing_count

    # 清除舊資料（重建時）
    if existing_count > 0:
        collection.delete(where={"id": {"$ne": ""}})
        print(f"🗑️  已清除舊有 {existing_count} 筆規則，開始重建...")

    rules = get_all_rules()
    ids, documents, metadatas = [], [], []

    for rule in rules:
        ids.append(rule["id"])
        documents.append(get_rag_indexing_text(rule["id"]) or rule["text"])
        metadatas.append({
            "level": rule["level"],
            "level_en": rule["level_en"],
            "color": rule["color"],
            "aqi_min": rule["aqi_range"][0] if rule["aqi_range"] else -1,
            "aqi_max": rule["aqi_range"][1] if rule["aqi_range"] else -1,
        })

    print(f"📡 正在對 {len(rules)} 筆規則進行 Embedding（text-embedding-3-small）...")

    # 批次呼叫 Embedding API
    response = _client.embeddings.create(
        model="openai/text-embedding-3-small",
        input=documents,
    )
    embeddings = [item.embedding for item in response.data]

    # 存入 ChromaDB
    collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )

    count = collection.count()
    print(f"✅ 健康規則知識庫建立完成，共 {count} 筆規則存入 ChromaDB。")
    return count


def query_knowledge_base(query_text: str, n_results: int = 3) -> list[dict]:
    """
    語意檢索健康規則知識庫。
    回傳最相關的 n_results 筆規則，每筆包含 document、metadata、distance。
    """
    collection = _get_collection()

    if collection.count() == 0:
        print("⚠️  知識庫為空，請先執行 build_knowledge_base()")
        return []

    # 將查詢文字轉為向量
    response = _client.embeddings.create(
        model="openai/text-embedding-3-small",
        input=[query_text],
    )
    query_embedding = response.data[0].embedding

    # 向量相似度搜尋
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(n_results, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    retrieved = []
    for i in range(len(results["ids"][0])):
        retrieved.append({
            "id": results["ids"][0][i],
            "document": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i],
        })

    return retrieved
