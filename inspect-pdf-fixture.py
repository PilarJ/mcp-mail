import json
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import reportlab
import pymupdf
out = Path(__file__).resolve().parent
font = TTFont('QAFontCoverage', str(Path(reportlab.__file__).parent / 'fonts/Vera.ttf'))
text = 'Žluťoučký kůň, příliš žluťoučký příběh. Číslo 14, částka 300 Kč.'
missing = sorted({char for char in text if ord(char) not in font.face.charWidths})
record = json.loads((out / 'formats-4b4a04b.json').read_text())
print(json.dumps({'missing_font_glyphs': missing, 'actual_extracted_text': record['responses']['české-stránky.pdf']['text']}, ensure_ascii=False))
# MuPDF includes a bundled Unicode font without obtaining a host font or network asset.
font_data = pymupdf.Font('cjk').buffer
font_path = out / 'qa-unicode-font.ttf'
font_path.write_bytes(font_data)
print('Bundled Unicode font saved:', len(font_data))
print('Missing in replacement:', [char for char in text if not pymupdf.Font(fontbuffer=font_data).has_glyph(ord(char))])
