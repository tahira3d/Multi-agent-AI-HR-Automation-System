import io
import zipfile
from app.services.resume_parser import parse_resume


def test_macro_bearing_docx_is_rejected_without_execution():
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w') as archive:
        archive.writestr('word/document.xml', '<document/>')
        archive.writestr('word/vbaProject.bin', b'SYNTHETIC NONEXECUTABLE MACRO MARKER')
    result = parse_resume('macro.docx', content.getvalue())
    assert result.status == 'rejected'
    assert result.text == ''


def test_compressed_docx_expansion_is_bounded():
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', b'x' * (31 * 1024 * 1024))
    assert len(content.getvalue()) < 1024 * 1024
    result = parse_resume('compressed.docx', content.getvalue())
    assert result.status == 'needs_manual_review'
    assert result.text == ''
