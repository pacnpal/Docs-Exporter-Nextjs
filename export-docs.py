import os
import markdown
import tempfile
import yaml
import re
import html
from git import Repo, RemoteProgress
from datetime import datetime
from packaging import version
from tqdm import tqdm
from playwright.sync_api import sync_playwright


def process_image_paths(md_content):
    # Define a regular expression pattern to find image tags
    pattern = r'src(?:Light|Dark)="(.*?)"'

    # Function to replace the relative path with an absolute path
    def replace(match):
        relative_path = match.group(1)
        absolute_path = f'{base_path}{relative_path}{path_args}'

        # Print the original and new image URLs for debugging
        return f'src="{absolute_path}"'

    # Use the sub method to replace all occurrences
    return re.sub(pattern, replace, md_content)


def preprocess_code_blocks(md_content):
    # Regular expression to match extended code blocks with filename and language
    pattern = r'```(\w+)?\s+filename="([^"]+)"\s*(switcher)?\n(.*?)```'

    def replace(match):
        language = match.group(1) if match.group(1) else ''
        filename = match.group(2)
        code_block = match.group(4)

        # Format the header with filename and language
        header = f'<div class="code-header"><i>{filename} ({language})</i></div>' if language else f'<div class="code-header"><i>{filename}</i></div>'

        return f'{header}\n```{language}\n{code_block}\n```'

    # Replace all occurrences in the content
    return re.sub(pattern, replace, md_content, flags=re.DOTALL)


def safe_load_frontmatter(frontmatter_content):
    try:
        return yaml.safe_load(frontmatter_content)
    except yaml.YAMLError:
        return None


def preprocess_mdx_content(md_content):
    # Replace HTML tags in frontmatter
    md_content = re.sub(r'<(/?\w+)>', lambda m: html.escape(m.group(0)), md_content)
    return md_content


def parse_frontmatter(md_content):
    lines = md_content.split('\n')
    if lines[0].strip() == '---':
        end_of_frontmatter = lines.index('---', 1)
        frontmatter = '\n'.join(lines[1:end_of_frontmatter])
        content = '\n'.join(lines[end_of_frontmatter + 1:])
        return frontmatter, content
    return None, md_content


class CloneProgress(RemoteProgress):
    def __init__(self):
        super().__init__()
        self.pbar = tqdm()

    def update(self, op_code, cur_count, max_count=None, message=''):
        if max_count is not None:
            self.pbar.total = max_count
        self.pbar.update(cur_count - self.pbar.n)

    def finalize(self):
        self.pbar.close()


def clone_repo(repo_url, branch, docs_dir, repo_dir):
    if not os.path.isdir(repo_dir):
        os.makedirs(repo_dir, exist_ok=True)
        print("Cloning repository...")
        repo = Repo.init(repo_dir)
        with repo.config_writer() as git_config:
            git_config.set_value("core", "sparseCheckout", "true")

        with open(os.path.join(repo_dir, ".git/info/sparse-checkout"), "w") as sparse_checkout_file:
            sparse_checkout_file.write(f"/{docs_dir}\n")

        origin = repo.create_remote("origin", repo_url)
        origin.fetch(progress=CloneProgress())
        repo.git.checkout(branch)
        print("Repository cloned.")
    else:
        print("Repository already exists. Updating...")
        repo = Repo(repo_dir)
        origin = repo.remotes.origin
        origin.fetch(progress=CloneProgress())
        repo.git.checkout(branch)
        origin.pull(progress=CloneProgress())
        print("Repository updated.")


def is_file_open(file_path):
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, 'a'):
            pass
        return False
    except PermissionError:
        return True


def get_files_sorted(root_dir):
    all_files = []
    for root, _, files in os.walk(root_dir):
        for file in files:
            full_path = os.path.join(root, file)
            modified_basename = '!!!' + file if file in ['index.mdx', 'index.md'] else file
            sort_key = os.path.join(root, modified_basename)
            all_files.append((full_path, sort_key))
    all_files.sort(key=lambda x: x[1])
    return [full_path for full_path, _ in all_files]


def preprocess_frontmatter(frontmatter):
    html_tags = {}

    def replace_tag(match):
        tag = match.group(0)
        placeholder = f"HTML_TAG_{len(html_tags)}"
        html_tags[placeholder] = tag
        return placeholder

    modified_frontmatter = re.sub(r'<[^>]+>', replace_tag, frontmatter)
    return modified_frontmatter, html_tags


def restore_html_tags(parsed_data, html_tags):
    if isinstance(parsed_data, dict):
        for key, value in parsed_data.items():
            if isinstance(value, str):
                for placeholder, tag in html_tags.items():
                    value = value.replace(placeholder, tag)
                value = html.escape(value)
                parsed_data[key] = value
    return parsed_data


def process_files(files, repo_dir, docs_dir):
    toc = ""
    html_all_pages_content = ""

    html_header = f"""
    <html>
    <head>
        <style>
            {open('styles.css').read()}
        </style>
    </head>
    <body>
    """

    numbering = [0]

    for index, file_path in enumerate(files):
        with open(file_path, 'r', encoding='utf8') as f:
            md_content = f.read()

            if Change_img_url:
                md_content = process_image_paths(md_content)

            md_content = preprocess_code_blocks(md_content)
            frontmatter, md_content = parse_frontmatter(md_content)

            if frontmatter:
                frontmatter, html_tags = preprocess_frontmatter(frontmatter)
                data = safe_load_frontmatter(frontmatter)
                if data is not None:
                    data = restore_html_tags(data, html_tags)
                    rel_path = os.path.relpath(file_path, os.path.join(repo_dir, docs_dir))
                    depth = rel_path.count(os.sep)
                    file_basename = os.path.basename(file_path)
                    if file_basename.startswith("index.") and depth > 0:
                        depth += -1
                    indent = '&nbsp;' * 5 * depth

                    while len(numbering) <= depth:
                        numbering.append(0)

                    numbering[depth] += 1

                    for i in range(depth + 1, len(numbering)):
                        numbering[i] = 0
                    
                    toc_numbering = f"{'.'.join(map(str, numbering[:depth + 1]))}"
                    toc_title = data.get('title', os.path.splitext(os.path.basename(file_path))[0].title())
                    toc_full_title = f"{toc_numbering} - {toc_title}"
                    toc += f"{indent}<a href='#{toc_full_title}'>{toc_full_title}</a><br/>"

                    html_page_content = f"""
                    <h1>{toc_full_title}</h1>
                    <div class="doc-path"><p>Documentation path: {file_path.replace(chr(92),'/').replace('.mdx', '').replace(repo_dir + '/' + docs_dir,'')}</p></div>
                    <p><strong>Description:</strong> {data.get('description', 'No description')}</p>
                    """
                    if data.get('related', {}):
                        html_page_content += f"""
                        <div style="margin-left:20px;">
                            <p><strong>Related:</strong></p>
                            <p><strong>Title:</strong> {data.get('related', {}).get('title', 'Related')}</p>
                            <p><strong>Related Description:</strong> {data.get('related', {}).get('description', 'No related description')}</p>
                            <p><strong>Links:</strong></p>
                        <ul>
                            {''.join([f'<li>{link}</li>' for link in data.get('related', {}).get('links', [])])}
                        </ul>
                        </div>
                        """
                    html_page_content += '</br>'
                else:
                    html_page_content = ""
            else:
                html_page_content = ""

            html_page_content += markdown.markdown(md_content, extensions=['fenced_code', 'codehilite', 'tables', 'footnotes', 'toc', 'abbr', 'attr_list', 'def_list', 'smarty', 'admonition'])
            html_all_pages_content += html_page_content

            if index < len(files) - 1:
                html_all_pages_content += '<div class="page-break"></div>'
    
    toc_html = f"""<div style="padding-bottom: 10px"><div style="padding-bottom: 20px"><h1>Table of Contents</h1></div>{toc}</div><div style="page-break-before: always;">"""
    html_all_content = toc_html + html_all_pages_content

    html_all_pages_content = html_header + html_all_pages_content + "</body></html>"
    toc_html = html_header + toc_html + "</body></html>"
    html_all_content = html_header + html_all_content + "</body></html>"

    return(html_all_content, toc_html, html_all_pages_content)


def find_latest_version(html_content):
    version_pattern = re.compile(r"v(\d+\.\d+\.\d+)")
    versions = version_pattern.findall(html_content)
    unique_versions = sorted(set(versions), key=lambda v: version.parse(v), reverse=True)
    return unique_versions[0] if unique_versions else None


def generate_pdf(html_content, output_pdf, format_options=None):
    """
    Generate PDF from HTML content using Playwright
    """
    default_format = {
        'format': 'A4',
        'margin': {
            'top': '50px',
            'right': '50px',
            'bottom': '50px',
            'left': '50px'
        },
        'print_background': True,
        'display_header_footer': True,
        'header_template': '<div style="font-size: 10px; text-align: right; width: 100%; padding-right: 20px; margin-top: 20px;"><span class="pageNumber"></span> of <span class="totalPages"></span></div>',
        'footer_template': '<div style="font-size: 10px; text-align: center; width: 100%; margin-bottom: 20px;"><span class="url"></span></div>'
    }
    
    format_options = format_options or default_format

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        
        # Set viewport size to ensure consistent rendering
        page.set_viewport_size({"width": 1280, "height": 1024})
        
        # Set content and wait for network idle
        page.set_content(html_content, wait_until='networkidle')
        
        # Wait for any images and fonts to load
        page.wait_for_load_state('networkidle')
        page.wait_for_load_state('domcontentloaded')
        
        # Generate PDF
        page.pdf(path=output_pdf, **format_options)
        
        browser.close()


if __name__ == "__main__":
    export_html = False

    repo_dir = "nextjs-docs" 
    repo_url = "https://github.com/vercel/next.js.git"
    branch = "canary"
    docs_dir = "docs"

    Change_img_url = True
    base_path = "https://nextjs.org/_next/image?url="
    path_args = "&w=1920&q=75"

    clone_repo(repo_url, branch, docs_dir, repo_dir)

    print("Converting the Documentation to HTML...")
    docs_dir_full_path = os.path.join(repo_dir, docs_dir)
    files_to_process = get_files_sorted(docs_dir_full_path)
    html_all_content, _, _ = process_files(files_to_process, repo_dir, docs_dir)
    print("Converted all MDX to HTML.")

    if export_html:
        with open('output.html', 'w', encoding='utf8') as f:
            f.write(html_all_content)
            print("HTML Content exported.")

    latest_version = find_latest_version(html_all_content)
    if latest_version:
        project_title = f"""Next.js Documentation v{latest_version}"""
        output_pdf = f"""Next.js_Docs_v{latest_version}_{datetime.now().strftime("%Y-%m-%d")}.pdf"""
    else:
        project_title = "Next.js Documentation"
        output_pdf = "Next.js_Documentation.pdf"

    cover_html = f"""
    <html>
        <head>
            <style>
                {open('styles.css').read()}
            </style>
        </head>
        <body>
            <div class="master-container">
                <div class="container">
                    <div class="title">{project_title}</div>
                    <div class="date">Date: {datetime.now().strftime("%Y-%m-%d")}</div>
                </div>
            </div>
        </body>
    </html>
    """

    format_options = {
                'format': 'A4',
                'margin': {
                    'top': '50px',
                    'right': '50px',
                    'bottom': '50px',
                    'left': '50px'
                },
                'print_background': True,
                'display_header_footer': True,
                'header_template': f'''
                    <div style="font-size: 10px; padding: 10px 20px; margin-top: 20px;">
                        <span style="float: left;">{project_title}</span>
                        <span style="float: right;">Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>
                    </div>
                ''',
                'footer_template': f'''
                    <div style="font-size: 10px; padding: 10px 20px; margin-bottom: 20px; text-align: center;">
                        Generated on {datetime.now().strftime("%Y-%m-%d")}
                    </div>
                '''
            }

            # Check if file is open
    if is_file_open(output_pdf):
                print("The output file is already open in another process. Please close it and try again.")
    else:
                try:
                    print("Generating PDF...")
                    # Generate PDF with cover page and content
                    generate_pdf(cover_html + html_all_content, output_pdf, format_options)
                    print("Created the PDF file successfully.")

                except Exception as e:
                    print(f"Error generating PDF: {str(e)}")