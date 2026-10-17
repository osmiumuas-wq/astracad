import builtins

from astracad_inspect.model import CheckOutcome
from astracad_inspect.validate import verify_pdf


def test_missing_pdf_reader_is_not_verifiable_not_a_crash(tmp_path, monkeypatch):
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    real = builtins.__import__

    def deny(name, *a, **k):
        if name in ("pypdfium2", "pymupdf"):
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", deny)
    checks = verify_pdf(None, pdf)
    assert len(checks) == 1 and checks[0].outcome is CheckOutcome.NOT_VERIFIABLE
