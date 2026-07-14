import re


def clean_for_tts(text):
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text)  # **bold**, *italic*
    text = re.sub(r'_{1,2}(.*?)_{1,2}', r'\1', text)     # __bold__, _italic_
    text = re.sub(r'`{1,3}.*?`{1,3}', '', text)          # `code`
    text = re.sub(r'#+\s*', '', text)                     # ## headings
    text = re.sub(r'^\s*\d+[\.\)]\s*', '', text)         # leading "1. " "2) " etc.
    text = re.sub(r'\n\s*\d+[\.\)]\s*', ' ', text)       # inline "\n2. "
    text = re.sub(r'\s+', ' ', text).strip()
    return text
