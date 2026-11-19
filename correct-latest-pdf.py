from pathlib import Path
out = Path(__file__).resolve().parent
path = out / '12c554f/check-formats.py'
source = path.read_text()
start = source.index("pdfmetrics.registerFont(TTFont('QAVera'")
end = source.index("doc = Document()", start)
source = source[:start] + '''import pymupdf
pdf_document = pymupdf.open()
font_path = OUT / 'qa-unicode-font.ttf'
for number in range(1, 4):
    page = pdf_document.new_page()
    page.insert_font(fontname='qa', fontfile=str(font_path))
    page.insert_text((30, 80), f'Strana {number}: {TEXT}', fontname='qa', fontsize=12)
pdf_document.subset_fonts()
add('české-stránky.pdf', 'application/pdf', pdf_document.tobytes(garbage=4, deflate=True))
''' + source[end:]
path.write_text(source)
