from pathlib import Path

from setuptools import find_packages, setup
from setuptools.command.build_py import build_py


class BuildWithDocumentation(build_py):
    """Copy repository guidance into the built package without changing sources."""

    def run(self) -> None:
        """Build modules and copy Markdown resources; propagate build/I/O errors."""
        super().run()
        package_dir = Path(self.build_lib) / "whatsapp_chatbot_python"
        for name in ("AGENTS.md", "ARCHITECTURE.md"):
            self.copy_file(name, str(package_dir / name))


with open("README.md", encoding="UTF-8") as file:
    long_description = file.read()

setup(
    name="whatsapp-chatbot-python",
    version="1.3.2",
    description=(
        "This library helps you easily create"
        " a Python chatbot with WhatsApp API."
    ),
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="GREEN API",
    author_email="support@green-api.com",
    url="https://github.com/green-api/whatsapp-chatbot-python",
    packages=find_packages(include=["whatsapp_chatbot_python", "whatsapp_chatbot_python.*"]),
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: English",
        "Natural Language :: Russian",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Communications",
        "Topic :: Communications :: Chat",
        "Topic :: Software Development",
        "Topic :: Software Development :: Libraries",
        "Topic :: Software Development :: Libraries :: Application Frameworks"
    ],
    cmdclass={"build_py": BuildWithDocumentation},
    license="MIT License",
    install_requires=["whatsapp-api-client-python>=0.0.53"],
    extras_require={
        "voip": [
            "whatsapp-api-client-python[voip]==0.0.55; python_version >= '3.11'",
            "openai==3.20.0; python_version >= '3.11'",
            "aiortc==1.15.0; python_version >= '3.11'",
            "av==17.1.0; python_version >= '3.11'",
            "websockets>=16,<17; python_version >= '3.11'",
        ],
    },
    python_requires=">=3.7"
)
