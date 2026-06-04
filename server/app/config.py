"""
全局配置入口：从 .env 读取，类型校验。
所有模块通过 from app.config import settings 获取配置。
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===== 豆包 API =====
    doubao_api_key: str
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/v3/"
    doubao_model: str
    doubao_fast_model: str = ""      # 轻量快速模型，用于意图分类/judge，不设则回退到 doubao_model
    doubao_fast_api_key: str = ""    # fast model 专用 API key，不设则复用 doubao_api_key
    doubao_embedding_model: str
    doubao_vision_model: str = ""

    # ===== 运行环境 =====
    environment: str = "development"

    # ===== 存储 =====
    vector_store: str = "chroma"
    chroma_persist_dir: str = "./chroma_db"
    db_type: str = "sqlite"
    sqlite_db_path: str = "./app.db"

    # ===== 数据集 =====
    dataset_dir: str = "../ecommerce_agent_dataset"

    # ===== Reranker =====
    reranker_model_path: str = "./models/Xorbits/bge-reranker-base"
    reranker_enabled: bool = True

    # ===== 服务 =====
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def dataset_path(self) -> Path:
        return Path(self.dataset_dir).resolve()


settings = Settings()
