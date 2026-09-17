"""Directory discovery and chapter boundaries for PDF textbooks."""
import re
from statistics import median

import pymupdf


CHAPTER = re.compile(r"^(项目\s*\d+|任务\s*\d+|模块\s*\d+|第\s*[一二三四五六七八九十百0-9]+\s*(?:章|单元)|Chapter\s+\d+|Unit\s+\d+)\s*(.*)$", re.I)
PAGE_LEADER = re.compile(r"^(.+?)[.．·…]{3,}\s*(\d+)\s*$", re.M)
SECTION = re.compile(r"^(\d+(?:[.．]\d+)+)\s+(.+)$")
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
    if len(result) > 500:
        raise ValueError("目录条目不能超过 500 条")
    result.sort(key=lambda item: item["pdf_page"])
    chapters = chapter_entries(result)
    if len({item["title"] for item in chapters}) != len(chapters):
        raise ValueError("章/项目标题重复，请添加编号")
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
        level = min(item["level"] for item in named)
        return [item for item in named if item["level"] == level]
    level = min(item["level"] for item in toc)
    return [item for item in toc if item["level"] == level]


def chapter_for_page(toc: list[dict], pdf_page: int) -> str:
    return next((item["title"] for item in reversed(chapter_entries(toc)) if item["pdf_page"] <= pdf_page), "前置内容")


def chapter_outline(toc: list[dict], chapter: str) -> list[dict]:
    """Return the selected chapter and its descendants with stable TOC indices."""
    chapter_ids = {id(item) for item in chapter_entries(toc)}
    roots = {index for index, item in enumerate(toc) if id(item) in chapter_ids}
    start = next((index for index in roots if toc[index]["title"] == chapter), None)
    if start is None:
        return []
    stop = next((index for index in range(start + 1, len(toc)) if index in roots), len(toc))
    root_level = toc[start]["level"]
    return [{**toc[index], "index": index} for index in range(start, stop)
            if index == start or toc[index]["level"] > root_level]


def expand_heading_indices(toc: list[dict], chapter: str, selected: list[int]) -> set[int]:
    outline = chapter_outline(toc, chapter)
    allowed = {item["index"] for item in outline}
    if not outline or not selected or any(index not in allowed for index in selected):
        raise ValueError("请选择当前章节中的目录标题")
    expanded = set(selected)
    positions = {item["index"]: offset for offset, item in enumerate(outline)}
    for index in selected:
        position = positions[index]
        level = outline[position]["level"]
        for item in outline[position + 1:]:
            if item["level"] <= level:
                break
            expanded.add(item["index"])
    return expanded


def headings_for_chunks(toc: list[dict], chapter: str, chunks: list, pages: dict) -> dict[int, int]:
    """Assign text blocks to TOC headings, using visible headings at shared page boundaries."""
    outline = chapter_outline(toc, chapter)
    if not outline:
        return {}
    current = outline[0]["index"]
    result = {}
    previous_page = None
    for chunk in chunks:
        pdf_page = pages[chunk.page_id].pdf_page
        if pdf_page != previous_page:
            earlier = [item for item in outline if item["pdf_page"] < pdf_page]
            current = earlier[-1]["index"] if earlier else outline[0]["index"]
            previous_page = pdf_page
        text = normalized(chunk.text)
        for item in outline:
            if item["pdf_page"] == pdf_page and normalized(item["title"]) in text:
                current = item["index"]
        result[chunk.id] = current
    return result


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
        section = SECTION.match(title)
        if title and printed > 0 and (CHAPTER.match(title) or section):
            level = 1 if CHAPTER.match(title) else min(8, section.group(1).replace("．", ".").count(".") + 1)
            entries.append({"level": level, "title": title, "printed_page": printed})
    if any(entry["title"].startswith("项目") for entry in entries):
        for entry in entries:
            if entry["title"].startswith("任务"):
                entry["level"] = 2
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
            result = vision(image, "按顺序提取目录中的章/项目及所有小节标题。返回 {\"items\":[{\"level\":1,\"title\":\"完整标题\",\"printed_page\":1}]}。章/项目为 level 1，其下小节为 level 2，再下一级为 level 3。目录页上的页码是印刷页码。")
            extracted.extend(result.get("items", []))
        for item in extracted:
            title = clean_title(str(item.get("title", "")))
            number = item.get("printed_page")
            if title and str(number).isdigit():
                entries.append({"level": max(1, min(8, int(item.get("level", 1)))), "title": title, "printed_page": int(number)})
    unique = {}
    for entry in entries:
        unique.setdefault((normalized(entry["title"]), entry["printed_page"], entry["level"]), entry)
    entries = list(unique.values())
    if not entries:
        raise RuntimeError("目录页已找到，但未识别出章/项目条目；请在目录编辑区人工录入")
    body_texts = [normalized(pdf[index].get_text()[:2000]) if index + 1 not in pages else "" for index in range(len(pdf))]
    major_matches = []
    for entry in entries:
        if entry["level"] != 1:
            continue
        title = normalized(entry["title"])
        body_page = next((index + 1 for index, text in enumerate(body_texts) if title and title in text), None)
        if body_page:
            major_matches.append(body_page - entry["printed_page"])
    offset = round(median(major_matches)) if major_matches else max(pages)
    if not major_matches:
        warnings.append("目录页码与 PDF 页码的偏移未核实，请逐条校对起始页")
    result = []
    for entry in entries:
        title = normalized(entry["title"])
        expected = entry["printed_page"] + offset
        candidates = [index + 1 for index, text in enumerate(body_texts) if title and title in text]
        entry["matched_page"] = min(candidates, key=lambda page: abs(page - expected)) if candidates else None
        page = entry["matched_page"] or entry["printed_page"] + offset
        if not entry["matched_page"]:
            if entry["level"] == 1:
                warnings.append(f"{entry['title']} 未在正文中找到标题，起始页为推算值")
        result.append({"level": entry["level"], "title": entry["title"], "pdf_page": min(max(1, page), len(pdf))})
    return validate_toc(result, len(pdf)), pages, raw[:30000], warnings
