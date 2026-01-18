from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Literal

from playwright.async_api import async_playwright

log = logging.getLogger("html2pdf")


@dataclass
class PdfOptions:
    format: Optional[str] = "A4"  # "A4", "Letter"...
    width: Optional[str] = None   # masalan "210mm"
    height: Optional[str] = None  # masalan "297mm"

    margin_top: str = "16mm"
    margin_right: str = "14mm"
    margin_bottom: str = "16mm"
    margin_left: str = "14mm"

    print_background: bool = True
    prefer_css_page_size: bool = True
    scale: float = 1.0

    display_header_footer: bool = False
    header_template: str = ""   # HTML template
    footer_template: str = ""   # HTML template


@dataclass
class RenderOptions:
    timeout_ms: int = 60_000
    wait_until: Literal["load", "domcontentloaded", "networkidle"] = "networkidle"
    emulate_media: Literal["screen", "print"] = "print"
    allow_network: bool = True  # True bo’lsa https resurslarni ham yuklaydi
    extra_css_path: Optional[Path] = None
    add_base_tag: bool = True   # relative resurslar uchun <base href="...">


class HtmlToPdfError(RuntimeError):
    pass


def _inject_base_tag(html: str, base_href: str) -> str:
    # <head> bo’lmasa ham ishlaydigan qilib qo’yamiz
    base_tag = f'<base href="{base_href}">'
    if "<head" in html.lower():
        # birinchi <head ...> dan keyin qo’shamiz
        import re
        return re.sub(r"(?i)(<head[^>]*>)", r"\1\n  " + base_tag, html, count=1)
    # head yo’q bo’lsa, yuqoridan minimal head qo’shib yuboramiz
    return f"<!doctype html><html><head>{base_tag}</head><body>{html}</body></html>"


def _wrap_with_css(html: str, css_text: str) -> str:
    style_tag = f"<style>\n{css_text}\n</style>"
    if "<head" in html.lower():
        import re
        return re.sub(r"(?i)(</head\s*>)", style_tag + r"\n\1", html, count=1)
    return f"<!doctype html><html><head>{style_tag}</head><body>{html}</body></html>"


async def html_to_pdf(
    *,
    html: str | None = None,
    html_path: Path | None = None,
    output_pdf_path: Path,
    base_dir: Path | None = None,
    pdf: PdfOptions = PdfOptions(),
    render: RenderOptions = RenderOptions(),
) -> Path:
    """
    HTML (string) yoki HTML faylni PDFga chiqaradi.

    - html: HTML matn (string)
    - html_path: HTML fayl yo’li (agar string bermasangiz)
    - base_dir: relative resurslar (./img.png, ./style.css) qaysi papkadan topilsin
    - output_pdf_path: chiqish pdf yo’li
    """
    if (html is None) == (html_path is None):
        raise ValueError("Faqat bittasini bering: html (string) yoki html_path (file).")

    output_pdf_path = output_pdf_path.resolve()
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    if html_path is not None:
        html_path = html_path.resolve()
        if not html_path.exists():
            raise FileNotFoundError(f"HTML topilmadi: {html_path}")
        html_text = html_path.read_text(encoding="utf-8")
        base_dir = base_dir or html_path.parent
    else:
        html_text = html or ""
        base_dir = base_dir or Path.cwd()

    # extra CSS (ixtiyoriy)
    if render.extra_css_path:
        css_path = render.extra_css_path.resolve()
        if not css_path.exists():
            raise FileNotFoundError(f"CSS topilmadi: {css_path}")
        html_text = _wrap_with_css(html_text, css_path.read_text(encoding="utf-8"))

    # relative resurslar (img/css) ishlashi uchun <base href="file:///.../">
    if render.add_base_tag:
        base_href = base_dir.resolve().as_uri() + "/"
        html_text = _inject_base_tag(html_text, base_href)

    # HTML string bilan ishlaganda eng ishonchli yo’l: vaqtinchalik .html faylga yozib, file:// orqali ochish
    with tempfile.TemporaryDirectory(prefix="html2pdf_") as tmp:
        tmp_html = Path(tmp) / "index.html"
        tmp_html.write_text(html_text, encoding="utf-8")

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()

            page = await context.new_page()

            # networkni bloklash kerak bo’lsa (masalan faqat lokal resurslar)
            if not render.allow_network:
                async def route_handler(route):
                    url = route.request.url
                    if url.startswith("file://"):
                        await route.continue_()
                    else:
                        await route.abort()
                await page.route("**/*", route_handler)

            # print rejimda CSS @media print ishlashi uchun
            await page.emulate_media(media=render.emulate_media)

            try:
                await page.goto(
                    tmp_html.as_uri(),
                    wait_until=render.wait_until,
                    timeout=render.timeout_ms,
                )

                # Fontlar yuklanishini kutamiz (PDFda "yo’qolib qolish" bo’lmasin)
                try:
                    await page.evaluate("() => document.fonts && document.fonts.ready")
                except Exception:
                    pass

                # PDF options
                pdf_kwargs = {
                    "path": str(output_pdf_path),
                    "print_background": pdf.print_background,
                    "prefer_css_page_size": pdf.prefer_css_page_size,
                    "scale": pdf.scale,
                    "margin": {
                        "top": pdf.margin_top,
                        "right": pdf.margin_right,
                        "bottom": pdf.margin_bottom,
                        "left": pdf.margin_left,
                    },
                }

                # format yoki width/height
                if pdf.width and pdf.height:
                    pdf_kwargs["width"] = pdf.width
                    pdf_kwargs["height"] = pdf.height
                else:
                    pdf_kwargs["format"] = pdf.format

                # header/footer
                if pdf.display_header_footer:
                    pdf_kwargs["display_header_footer"] = True
                    pdf_kwargs["header_template"] = pdf.header_template
                    pdf_kwargs["footer_template"] = pdf.footer_template

                await page.pdf(**pdf_kwargs)

            except Exception as e:
                raise HtmlToPdfError(f"PDF render xatosi: {e}") from e
            finally:
                await context.close()
                await browser.close()

    return output_pdf_path


def _default_footer() -> str:
    # Playwright header/footer template ichida maxsus klasslar ishlaydi:
    # .pageNumber, .totalPages, .date, .title, .url
    return """
    <div style="width:100%; font-size:9px; color:#666; padding:0 10mm;">
      <div style="float:left;">Generated</div>
      <div style="float:right;">
        <span class="pageNumber"></span> / <span class="totalPages"></span>
      </div>
    </div>
    """


def parse_args():
    ap = argparse.ArgumentParser(description="Professional HTML -> PDF (Playwright/Chromium)")
    ap.add_argument("input", help="HTML fayl yo’li (masalan page.html)")
    ap.add_argument("-o", "--output", default="out.pdf", help="Chiqish PDF (default: out.pdf)")
    ap.add_argument("--base-dir", default=None, help="Relative resurslar uchun base papka (default: html fayl papkasi)")
    ap.add_argument("--css", default=None, help="Qo’shimcha CSS fayl (ixtiyoriy)")
    ap.add_argument("--no-network", action="store_true", help="Internet resurslarni bloklash (faqat file://)")
    ap.add_argument("--timeout", type=int, default=60000, help="Timeout ms (default: 60000)")
    ap.add_argument("--format", default="A4", help="PDF format (default: A4)")
    ap.add_argument("--margin", default="16mm,14mm,16mm,14mm", help="top,right,bottom,left (default: 16mm,14mm,16mm,14mm)")
    ap.add_argument("--header-footer", action="store_true", help="Header/Footer yoqish (page number bilan)")
    ap.add_argument("--wait-until", default="networkidle", choices=["load", "domcontentloaded", "networkidle"])
    return ap.parse_args()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()

    html_path = Path(args.input)
    out_pdf = Path(args.output)

    base_dir = Path(args.base_dir) if args.base_dir else None
    css_path = Path(args.css) if args.css else None

    try:
        top, right, bottom, left = [x.strip() for x in args.margin.split(",")]
    except Exception:
        print("Margin formati noto’g’ri. Masalan: 16mm,14mm,16mm,14mm")
        sys.exit(2)

    pdf_opts = PdfOptions(
        format=args.format,
        margin_top=top,
        margin_right=right,
        margin_bottom=bottom,
        margin_left=left,
    )

    if args.header_footer:
        pdf_opts.display_header_footer = True
        pdf_opts.footer_template = _default_footer()

    render_opts = RenderOptions(
        timeout_ms=args.timeout,
        wait_until=args.wait_until,
        allow_network=not args.no_network,
        extra_css_path=css_path,
    )

    async def runner():
        result = await html_to_pdf(
            html_path=html_path,
            output_pdf_path=out_pdf,
            base_dir=base_dir,
            pdf=pdf_opts,
            render=render_opts,
        )
        log.info(f"OK: {result}")

    asyncio.run(runner())


if __name__ == "__main__":
    main()
