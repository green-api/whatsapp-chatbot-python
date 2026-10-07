from setuptools import find_packages, setup

with open("README.md", encoding="UTF-8") as file:
    long_description = file.read()

setup(
    name="whatsapp-chatbot-python",
    version="0.9.9",
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
        "License :: Other/Proprietary License",
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
    license=(
        "Creative Commons Attribution-NoDerivatives 4.0 International"
        " (CC BY-ND 4.0)"
    ),
    install_requires=["whatsapp-api-client-python>=0.0.53"],
    extras_require={
        "voip": [
            "whatsapp-api-client-python[voip]==0.0.55",
            "openai==3.20.0",
            "aiortc==1.15.0",
            "av==17.1.0",
        ],
    },
    python_requires=">=3.7"
)
