"""Fails a test run that skipped anything: every test runs on every platform.

  skips.py <pytest junit xml>
"""

import sys
import xml.etree.ElementTree as ElementTree


def main(path: str) -> int:
    root = ElementTree.parse(path).getroot()
    skipped = [(case.get('classname'), case.get('name'), skip.get('message', ''))
               for case in root.iter('testcase') for skip in case.findall('skipped')]
    print(f'{len(skipped)} skipped')
    for classname, name, message in skipped:
        print(f'  {classname}.{name}: {message}')
    return 1 if skipped else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
