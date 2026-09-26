"""STITCH-04 acceptance criteria for the review composer template.

Asserts on the template/CSS source (the page is a static shell), so no app
import is needed.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "app/templates/customer/review_composer.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/review-composer.css").read_text(encoding="utf-8")


def test_five_stars_are_60px():
    assert HTML.count("h-[60px] w-[60px]") == 1  # one button inside a range(1, 6) loop
    assert "range(1, 6)" in HTML
    assert 'width="60" height="60"' in HTML


def test_tag_chips_use_doc4_classes():
    assert "bg-[#EFF6FF]" in HTML
    assert "text-[#1A56DB]" in HTML
    assert "border border-[#1A56DB]/20" in HTML


def test_ai_draft_card_hidden_by_default():
    assert '<div id="ai-draft-card" class="hidden' in HTML


def test_ai_draft_text_is_georgia_17px():
    assert "Georgia" in CSS
    assert "font-size: 17px" in CSS
    assert 'id="ai-draft-text" class="ai-draft-text' in HTML


def test_copy_button_has_clipboard_target():
    assert 'data-clipboard-target="#ai-draft-text"' in HTML
