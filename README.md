# HTML to PDF (Python)

HTML faylni professional tarzda PDFga aylantiradi (Chromium/Playwright).
CSS, rasmlar, jadval, print background yaxshi ishlaydi.

## O’rnatish:
```bash
pip install playwright
playwright install chromium
```
## Yurgazish oddiy:
```
python html_to_pdf.py page.html -o out.pdf
```

## Qo’shimcha CSS:
```
python html_to_pdf.py page.html --css style.css -o out.pdf
```
