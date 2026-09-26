"""P2 regression contract: AI identity, capability disclosure, Gemini and TTS ownership."""
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_chat_and_planner_use_affiliate2_identity_and_truthful_scope():
    chat = _read("frontend/src/pages/Chat.jsx")
    planner = _read("backend/agents/planner.py")
    api = _read("backend/api/chat.py")

    assert "Affiliate2 AI" in chat
    assert "ViralMint" not in chat
    assert "Affiliate2 AI action assistant" in planner
    assert "You are ViralMint" not in planner
    assert "You can do ANYTHING" not in planner
    assert "1000+ sites" not in planner
    assert "PLANNER_ACTION_TYPES" in planner
    assert "Không phải computer-use agent" in planner
    assert '@router.get("/api/chat/capabilities")' in api
    assert 'http.get("/api/chat/capabilities")' in chat


def test_smart_cut_discloses_effective_global_ai_runtime():
    ui = _read("frontend/src/pages/ClipStudio.jsx")
    assert 'http.get("/api/settings/ai/effective")' in ui
    assert "AI chọn đoạn:" in ui
    assert "Smart Cut dùng AI runtime toàn cục" in ui


def test_gemini_is_a_compatible_preset_not_a_second_runtime():
    ui = _read("frontend/src/components/settings/AIProviderSection.jsx")
    backend = _read("backend/core/ai_provider.py")

    assert "Google Gemini (AI Studio)" in ui
    assert "https://generativelanguage.googleapis.com/v1beta/openai/" in ui
    assert '"gemini": "https://generativelanguage.googleapis.com/v1beta/openai/"' in backend
    assert 'provider=AIProvider.COMPATIBLE' in backend


def test_localization_ui_separates_ai_tts_and_audio_layers():
    tts = _read("frontend/src/components/settings/TTSProviderSection.jsx")
    profiles = _read("frontend/src/components/settings/LocalizationProfilesSection.jsx")

    assert "Đây chỉ là kết nối TTS dùng chung" in tts
    assert "1. AI dịch C6" in tts
    assert "2. AI viết lại C7" in tts
    assert "3. TTS tạo giọng" in tts
    assert "4. Audio / render / QC" in tts
    assert "TTS tùy chỉnh tương thích OpenAI" in tts

    assert "Hồ sơ bản địa hóa ghép 4 lớp độc lập" in profiles
    assert "Viết lại thời lượng (C7) & TTS" in profiles
    assert "Ghi đè TTS cho hồ sơ" in profiles
    assert "Audio treatment / Kết xuất / QC" in profiles
