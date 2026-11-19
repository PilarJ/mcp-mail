from pathlib import Path
out = Path(__file__).resolve().parent
latest = out / '12c554f'
for name in ['check-independent.py', 'check-formats.py', 'check-scan-rotation.py', 'check-czech-pdf.py', 'check-container.py']:
    source = (out / name).read_text()
    source = source.replace('4b4a04b825667bc65237267563f2b99fa494fbbb', '12c554f5abe89e30980d5399e1e28329ee3942de').replace('QA-followup-4b4a04b', 'QA-followup-12c554f').replace('QA-rotation-followup', 'QA-rotation-12c554f')
    if name == 'check-formats.py':
        source = source.replace("str(Path(reportlab.__file__).parent / 'fonts/Vera.ttf')", "str(OUT / 'qa-unicode-font.ttf')")
        # ReportLab accepts TrueType outlines only; the corrected PDF is separately made by MuPDF.
        source = source.replace("str(OUT / 'qa-unicode-font.ttf')", "str(Path(reportlab.__file__).parent / 'fonts/Vera.ttf')")
    (latest / name).write_text(source)
for name in ['qa.crt', 'qa-unicode-font.ttf']:
    (latest / name).write_bytes((out / name).read_bytes())
