#!/usr/bin/env python3
from setuptools import setup

setup(
    name="asus-thermals-control",
    version="1.2.0",
    description="Native Linux hardware telemetry monitor and platform profile manager",
    author="Yashwanth Ponnam",
    author_email="yashwanthponnam55@gmail.com",
    url="https://github.com/Yash55-max/asus-thermals-control",
    license="MIT",
    py_modules=["app"],
    entry_points={
        "console_scripts": [
            "thermals=app:main",
            "asus-thermals-control=app:main",
        ],
    },
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: X11 Applications",
        "Environment :: Console",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Topic :: System :: Hardware",
        "Topic :: System :: Monitoring",
    ],
)
