"""Docs must survive a clean clone: no links to ignored reports or retired setup."""
from pathlib import Path
import re
import unittest

REPO = Path(__file__).resolve().parents[2]


class RepositoryTests(unittest.TestCase):
    def test_document_links_resolve_from_source_checkout(self):
        docs = [REPO / 'README.md', *(REPO / 'localstack').glob('*.md'),
                *(REPO / 'localstack/business').glob('*.md'), *(REPO / 'localstack/scaling').glob('*.md'),
                *(REPO / 'localstack/observability').glob('*.md'), *(REPO / 'aws').glob('*.md')]
        for doc in docs:
            for target in re.findall(r'\]\(([^)]+)\)', doc.read_text()):
                if '://' in target or target.startswith('#'): continue
                path = (doc.parent / target.split('#')[0]).resolve()
                with self.subTest(doc=str(doc.relative_to(REPO)), target=target):
                    self.assertTrue(path.exists(), str(path))
                    self.assertNotIn('reports', path.relative_to(REPO).parts)

    def test_aws_contains_design_only(self):
        files = [p.relative_to(REPO / 'aws').as_posix() for p in (REPO / 'aws').rglob('*') if p.is_file() and p.name != '.DS_Store' and '__pycache__' not in p.parts]
        self.assertEqual(set(files), {'README.md', 'ARCHITECTURE.md'})
        self.assertFalse((REPO / 'docker-compose.yml').exists())
        self.assertFalse((REPO / 'statemachine/docs-orchestrator.asl.json').exists())


if __name__ == '__main__': unittest.main()
