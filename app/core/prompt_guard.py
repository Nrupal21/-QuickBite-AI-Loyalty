"""QuickBite — LLM input sanitisation.

Sanitises user input before passing to OpenAI/Gemini to prevent
prompt injection attacks. Strips markdown, HTML, and control characters.
"""
