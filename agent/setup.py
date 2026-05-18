from setuptools import setup, find_packages
setup(
    name="qgis-agent",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "fastapi[standard]",
        "uvicorn[standard]",
        "httpx",
        "aiosqlite",
        "sqlite-vec",
        "agno",
        "jinja2",
        "python-multipart",
        "pyyaml",
        "pyjwt[crypto]",
    ],
)
