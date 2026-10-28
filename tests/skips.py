"""Fails a test run that skipped anything it could have run.

  skips.py <pytest junit xml>

The suite runs everything on Windows, where Windows PowerShell 5.1 runs the PowerShell installer, bootstrap and
uninstaller. On macOS and Linux only those PowerShell tests skip, each with WINDOWS_ONLY as its reason, so a run
there passes only when every skip says that.
"""

import sys
import xml.etree.ElementTree as ElementTree

WINDOWS_ONLY = 'Windows PowerShell 5.1 runs only on Windows'


def main(path: str) -> int:
    root = ElementTree.parse(path).getroot()
    skipped = [(case.get('classname'), case.get('name'), skip.get('message', ''))
               for case in root.iter('testcase') for skip in case.findall('skipped')]
    unexpected = [skip for skip in skipped if sys.platform == 'win32' or skip[2] != WINDOWS_ONLY]
    print(f'{len(skipped)} skipped, {len(unexpected)} of them not for the platform')
    for classname, name, message in unexpected:
        print(f'  {classname}.{name}: {message}')
    return 1 if unexpected else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
