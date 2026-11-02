from app.services.pdf_service import find_highlight_rects

pdf_path = r"C:\Users\DELL\Downloads\documents\MP-LAB-SYN-002\MP-LAB-SYN-002_01_application_searchable.pdf"

quote = (
    "The applicant states that the dispute arose during "
    "the course of employment with Vindhya Retail Services "
    "(Fictional)."
)

rectangles = find_highlight_rects(
    pdf_path=pdf_path,
    page_number=1,
    quote=quote,
)

print("Number of rectangles:", len(rectangles))

for rectangle in rectangles:
    print(rectangle)