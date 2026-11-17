"""Use the document name in the table row, not just 'Download PDF'."""
import re


def link_title(anchor):
    label=anchor.get_text(' ',strip=True)
    if re.fullmatch(r'(?:скачать|открыть|перейти|pdf|docx?|xlsx?|\(|\)|\s|[/—-])+',label,re.I):
        row=anchor.find_parent('tr')
        if row:
            cells=row.find_all(['td','th'],recursive=False)
            for cell in cells:
                text=cell.get_text(' ',strip=True)
                if 12<len(text)<1200 and not cell.find('a'):
                    return text
    return label
