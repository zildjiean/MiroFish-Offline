"""
Configuration Management
Loads configuration from .env file in project root directory
"""

import os
from dotenv import load_dotenv

# Load .env file from project root
# Path: MiroFish/.env (relative to backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # If no .env in root, try to load environment variables (for production)
    load_dotenv(override=True)


class Config:
    """Flask configuration class"""

    # Flask configuration
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'

    # JSON configuration - disable ASCII escaping to display Chinese directly (not as \uXXXX)
    JSON_AS_ASCII = False

    # LLM configuration (unified OpenAI format)
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'http://localhost:11434/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'qwen2.5:32b')
    # Completion budget per request. Reasoning models spend part of it thinking before
    # emitting anything, so 4096 could leave nothing for the answer on Thai input and
    # the call came back with content=None (finish_reason=length).
    LLM_MAX_TOKENS = int(os.environ.get('LLM_MAX_TOKENS', '8192'))

    # Per-request timeout. 300s meant one hung call could block a batch for 5 minutes,
    # and with retries a single agent-config batch stalled for over half an hour.
    # Failing fast and retrying recovers quicker than waiting out a stuck request.
    LLM_TIMEOUT = float(os.environ.get('LLM_TIMEOUT', '120'))

    # Entity extraction is structured output, not prose, and a smaller non-verbose model
    # handles it faster and more reliably than one that reasons at length. Empty means
    # "use LLM_MODEL_NAME", which reproduces upstream behaviour.
    # Caveat: models classify entity types differently, so mixing one model's NER into a
    # graph built by another can produce inconsistent types for the same entity.
    NER_MODEL_NAME = os.environ.get('NER_MODEL_NAME', '').strip()

    # /prepare regenerates simulation_config.json on every run, so editing that file by
    # hand does not survive. These cap what the LLM produces, and are the two values that
    # actually drive cost and memory: how many agents wake per round, and how much
    # simulated time a round covers. 0 means "leave the generated value alone".
    SIM_AGENTS_PER_HOUR_MAX = int(os.environ.get('SIM_AGENTS_PER_HOUR_MAX', '0'))
    SIM_MINUTES_PER_ROUND = int(os.environ.get('SIM_MINUTES_PER_ROUND', '0'))

    # Neo4j configuration
    NEO4J_URI = os.environ.get('NEO4J_URI', 'bolt://localhost:7687')
    NEO4J_USER = os.environ.get('NEO4J_USER', 'neo4j')
    NEO4J_PASSWORD = os.environ.get('NEO4J_PASSWORD', 'mirofish')

    # Embedding configuration
    EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL', 'nomic-embed-text')
    EMBEDDING_BASE_URL = os.environ.get('EMBEDDING_BASE_URL', 'http://localhost:11434')
    # Embedding transport: 'openai' (POST /v1/embeddings) or 'ollama' (POST /api/embed)
    EMBEDDING_API_STYLE = os.environ.get('EMBEDDING_API_STYLE', 'openai')
    # Falls back to the LLM key so a single gateway credential covers both
    EMBEDDING_API_KEY = os.environ.get('EMBEDDING_API_KEY') or os.environ.get('LLM_API_KEY')
    # Must match the Neo4j vector index dimension (see storage/neo4j_schema.py)
    EMBEDDING_DIM = int(os.environ.get('EMBEDDING_DIM', '768'))
    # E5-family models expect 'query: ' / 'passage: '; leave empty for other models
    EMBEDDING_TEXT_PREFIX = os.environ.get('EMBEDDING_TEXT_PREFIX', '')
    # CPU-only host — cap torch threads so embedding doesn't starve Flask
    EMBEDDING_TORCH_THREADS = int(os.environ.get('EMBEDDING_TORCH_THREADS', '4'))

    # Output language for generated free text (personas, agent posts, reports).
    # 'en' reproduces upstream behaviour; see app/utils/language.py
    CONTENT_LANGUAGE = os.environ.get('CONTENT_LANGUAGE', 'en')

    # File upload configuration
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}

    # Text processing configuration
    DEFAULT_CHUNK_SIZE = 500  # Default chunk size
    DEFAULT_CHUNK_OVERLAP = 50  # Default overlap size

    # OASIS simulation configuration
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')

    # OASIS platform available actions configuration
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]

    # Report Agent configuration
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))

    @classmethod
    def validate(cls):
        """Validate required configuration"""
        errors = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY not configured (set to any non-empty value, e.g. 'ollama')")
        if not cls.NEO4J_URI:
            errors.append("NEO4J_URI not configured")
        if not cls.NEO4J_PASSWORD:
            errors.append("NEO4J_PASSWORD not configured")
        return errors
