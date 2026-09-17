"""Directory discovery and chapter boundaries for PDF textbooks."""
import re
from statistics import median

import pymupdf


CHAPTER = re.compile(r"^(项目\s*\d+|任务\s*\d+|模块\s*\d+|第\s*[一二三四五六七八九十百0-9]+\s*(?:章|单元)|Chapter\s+\d+|Unit\s+\d+)\s*(.*)$", re.I)
PAGE_LEADER = re.compile(r"^(.+?)[.．·…]{3,}\s*(\d+)\s*$", re.M)
TOC_HEADING = re.compile(r"(^|\n)\s*(目\s*录|contents|table of contents)\b", re.I)


def clean_title(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" .·…：:—-\t")


def normalized(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def parse_page_spec(value: str, page_count: int) -> list[int]:
    pages = set()
    for part in value.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        numbers = range(int(match[1]), int(match[2]) + 1) if match else [int(part)] if part.isdigit() else []
        if not numbers:
            raise ValueError("目录页范围格式应为 6-8,10")
        pages.update(numbers)
    if any(page < 1 or page > page_count for page in pages) or len(pages) > 20:
        raise ValueError("目录页必须在 PDF 范围内，且最多 20 页")
    return sorted(pages)


def validate_toc(items: list[dict], page_count: int) -> list[dict]:
    result = []
    for item in items:
        title = clean_title(str(item.get("title", "")))
        try:
            level = int(item.get("level", 1))
            page = int(item.get("pdf_page", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("目录层级和 PDF 起始页必须是数字") from exc
        if not title or not 1 <= level <= 8 or not 1 <= page <= page_count:
            raise ValueError("目录标题、层级或 PDF 起始页无效")
        result.append({"level": level, "title": title, "pdf_page": page})
    if len(result) > 500 or len({item["title"] for item in result}) != len(result):
        raise ValueError("目录条目过多或章节标题重复，请为重复章节添加编号")
    result.sort(key=lambda item: item["pdf_page"])
    if any(result[i]["pdf_page"] > result[i + 1]["pdf_page"] for i in range(len(result) - 1)):
        raise ValueError("目录页码顺序无效")
    return result


def parse_manual_toc(value: str, page_count: int) -> list[dict]:
    items = []
    for number, line in enumerate(value.splitlines(), 1):
        if not line.strip():
            continue
        parts = [piece.strip() for piece in line.split("|")]
        if len(parts) == 2:
            parts.insert(0, "1")
        if len(parts) != 3:
            raise ValueError(f"手工目录第 {number} 行应为 层级|章节标题|PDF起始页")
        items.append({"level": parts[0], "title": parts[1], "pdf_page": parts[2]})
    return validate_toc(items, page_count)


def chapter_entries(toc: list[dict]) -> list[dict]:
    if not toc:
        return []
    named = [item for item in toc if CHAPTER.match(item["title"])]
    if named:
        return named
    level = min(item["level"] for item in toc)
    return [item for item in toc if item["level"] == level]


def chapter_for_page(toc: list[dict], pdf_page: int) -> str:
    return next((item["title"] for item in reversed(chapter_entries(toc)) if item["pdf_page"] <= pdf_page), "前置内容")


def parse_toc_text(text: str) -> list[dict]:
    entries = []
    pending = ""
    for raw in text.splitlines():
        line = clean_title(raw)
        chapter = CHAPTER.match(line)
        if chapter:
            pending = clean_title(chapter.group(1))
            line = clean_title(chapter.group(2))
        match = PAGE_LEADER.match(line)
        if not match:
            continue
        title, printed = clean_title(match.group(1)), int(match.group(2))
        if pending:
            title = clean_title(f"{pending} {title}")
            pending = ""
        if CHAPTER.match(title) and title and printed > 0:
            entries.append({"level": 1, "title": title, "printed_page": printed})
    return entries


def _contact_sheet(pdf: pymupdf.Document, start: int, stop: int) -> bytes:
    sheet = pymupdf.open()
    page = sheet.new_page(width=1200, height=1600)
    for offset, index in enumerate(range(start, stop)):
        column, row = offset % 3, offset // 3
        x, y = column * 400, row * 400
        page.insert_text((x + 8, y + 20), f"PDF {index + 1}", fontsize=15)
        page.show_pdf_page(pymupdf.Rect(x + 8, y + 28, x + 392, y + 390), pdf, index)
    png = page.get_pixmap(matrix=pymupdf.Matrix(1, 1)).tobytes("png")
    sheet.close()
    return png


def discover_catalog(pdf: pymupdf.Document, hints: list[int], vision) -> tuple[list[dict], list[int], str, list[str]]:
    warnings = []
    pages = list(hints)
    if not pages:
        for index in range(min(35, len(pdf))):
            text = pdf[index].get_text()
            if TOC_HEADING.search(text[:500]):
                pages = [index + 1]
                for next_index in range(index + 1, min(index + 12, len(pdf))):
                    next_text = pdf[next_index].get_text()
                    if len(PAGE_LEADER.findall(next_text)) < 2:
                        break
                    pages.append(next_index + 1)
                break
    if not pages:
        for start in range(0, min(24, len(pdf)), 12):
            response = vision(_contact_sheet(pdf, start, min(start + 12, len(pdf))),
                "这是带 PDF 页码标签的教材缩略图。找出目录/Contents 所在 PDF 页。返回 {\"pdf_pages\":[页码]}；不要把正文页当目录。")
            pages.extend(int(value) for value in response.get("pdf_pages", []) if str(value).isdigit())
        pages = sorted({page for page in pages if 1 <= page <= len(pdf)})[:20]
    if not pages:
        raise RuntimeError("未找到目录，请手工指定目录页范围或录入章节与 PDF 起始页")
    raw = "\n".join(pdf[number - 1].get_text() for number in pages)
    entries = parse_toc_text(raw)
    visible_headers = sum(bool(CHAPTER.match(clean_title(line))) for line in raw.splitlines())
    if entries and len(entries) < visible_headers:
        warnings.append(f"目录中检测到 {visible_headers} 个章/项目标题，只解析出 {len(entries)} 条，请检查遗漏")
    if not entries:
        warnings.append("目录由视觉模型识别，请逐条核对章节名称和起始页")
        extracted = []
        for number in pages:
            image = pdf[number - 1].get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).tobytes("png")
            result = vision(image, "提取目录中的章/项目级条目，不要提取小节。返回 {\"items\":[{\"title\":\"完整章标题\",\"printed_page\":1}]}。目录页上的页码是印刷页码。")
            extracted.extend(result.get("items", []))
        for item in extracted:
            title = clean_title(str(item.get("title", "")))
            number = item.get("printed_page")
            if title and str(number).isdigit():
                entries.append({"level": 1, "title": title, "printed_page": int(number)})
    unique = {}
    for entry in entries:
        unique.setdefault(normalized(entry["title"]), entry)
    entries = list(unique.values())
    if not entries:
        raise RuntimeError("目录页已找到，但未识别出章/项目条目；请在目录编辑区人工录入")
    body_texts = [normalized(pdf[index].get_text()[:1200]) if index + 1 not in pages else "" for index in range(len(pdf))]
    matched = []
    for entry in entries:
        title = normalized(entry["title"])
        body_page = next((index + 1 for index, text in enumerate(body_texts) if title and title in text), None)
        entry["matched_page"] = body_page
        if body_page:
            matched.append(body_page - entry["printed_page"])
    offset = round(median(matched)) if matched else max(pages)
    if not matched:
        warnings.append("目录页码与 PDF 页码的偏移未核实，请逐条校对起始页")
    result = []
    for entry in entries:
        page = entry["matched_page"] or entry["printed_page"] + offset
        if not entry["matched_page"]:
            warnings.append(f"{entry['title']} 未在正文中找到标题，起始页为推算值")
        result.append({"level": 1, "title": entry["title"], "pdf_page": min(max(1, page), len(pdf))})
    return validate_toc(result, len(pdf)), pages, raw[:30000], warnings
