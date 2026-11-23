"""Additional entirely synthetic candidates and three job descriptions."""
import json
from pathlib import Path
from docx import Document

ROOT = Path(__file__).resolve().parents[1]
JOBS = [
    {'job_title': 'Backend Python Developer', 'job_description': 'SYNTHETIC DEMO ROLE. Develop Python and FastAPI REST APIs backed by PostgreSQL. Write pytest suites, document APIs and collaborate using Git. Demonstrated project delivery matters more than keyword repetition.',
     'required_skills': ['Python', 'FastAPI', 'PostgreSQL'], 'preferred_skills': ['Docker', 'Azure'], 'min_experience_years': 2, 'location': 'Remote', 'work_arrangement': 'remote',
     'screening_questions': ['Describe a Python API you delivered.', 'Explain a PostgreSQL query you improved.']},
    {'job_title': 'Data Analyst', 'job_description': 'SYNTHETIC DEMO ROLE. Analyze operational datasets using SQL and Python pandas, validate data quality, document assumptions and communicate findings in dashboards. Relevant analytical project evidence is required.',
     'required_skills': ['SQL', 'Python', 'pandas'], 'preferred_skills': ['Power BI', 'PostgreSQL'], 'min_experience_years': 1, 'location': 'Synthetic office', 'work_arrangement': 'hybrid',
     'screening_questions': ['How did you validate a dataset?', 'Explain an analysis that affected a business decision.']},
    {'job_title': 'Frontend React Developer', 'job_description': 'SYNTHETIC DEMO ROLE. Develop accessible React interfaces with TypeScript, integrate REST APIs, write component tests and collaborate through Git. Assess demonstrated user-interface implementation and accessibility work.',
     'required_skills': ['React', 'TypeScript', 'CSS'], 'preferred_skills': ['Testing Library', 'Accessibility'], 'min_experience_years': 2, 'location': 'Remote', 'work_arrangement': 'remote',
     'screening_questions': ['Explain a React component you implemented.', 'How do you test keyboard accessibility?']},
]
PROFILES = [
    ('21_taylor_synthetic.docx', 'Taylor Synthetic', 'Strong backend evidence', [
        'Developed Python and FastAPI REST APIs using PostgreSQL for an inventory service.',
        'Backend developer, Fictional Demo Studio, 2021-2026. Wrote pytest coverage and Docker deployment documentation.',
        'Project: implemented transaction-safe stock reservations, authentication and structured API errors.',
        'Education: computer science coursework; certification details are not supplied.']),
    ('22_jordan_synthetic.docx', 'Jordan Synthetic', 'Moderate analytical evidence', [
        'Used Python pandas and SQL to clean a synthetic sales dataset and document missing values.',
        'Project: built a dashboard comparing monthly sales and reconciled totals against SQL queries.',
        'A short internship involved preparing validation reports; exact dates are not supplied.',
        'Formal qualification and certification details are not provided.']),
    ('23_casey_synthetic.docx', 'Casey Synthetic', 'Incomplete frontend evidence', [
        'Built a React component for a synthetic appointment form using TypeScript and CSS.',
        'The project mentions API integration but does not describe testing or accessibility outcomes.',
        'Employment duration, education and certification details are not provided.']),
]


def main():
    destination = ROOT / 'sample_data' / 'phase2_resumes'
    destination.mkdir(parents=True, exist_ok=True)
    for index, (filename, name, label, paragraphs) in enumerate(PROFILES):
        document = Document()
        document.add_heading(name, level=1)
        document.add_paragraph(f'{name.split()[0].lower()}@synthetic.example.test')
        document.add_paragraph('SYNTHETIC CLASSROOM DATA. All persons and claims are fictional.')
        document.add_heading(label, level=2)
        if index == 1:
            table = document.add_table(rows=0, cols=1)
            for paragraph in paragraphs:
                table.add_row().cells[0].text = paragraph
        else:
            for paragraph in paragraphs:
                document.add_paragraph(paragraph)
        document.save(destination / filename)
    (ROOT / 'sample_data' / 'jobs.json').write_text(json.dumps(JOBS, indent=2), encoding='utf-8')
    (destination / 'README.md').write_text('All candidates, contact addresses and experience in this folder are fictional synthetic demonstration data. Includes strong, moderate and incomplete evidence and table-based formatting.\n', encoding='utf-8')
    print('Generated three synthetic job descriptions and three additional DOCX candidates.')


if __name__ == '__main__':
    main()
