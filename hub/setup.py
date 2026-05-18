from setuptools import setup, find_packages

setup(
    name="qgis-mcp-hub",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "fastapi",
        "uvicorn[standard]",
        "httpx",
        "PyJWT[crypto]",
        "aiosqlite",
    ],
)
