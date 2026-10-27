import unittest
from scripts.verify import check

class SkillContractTests(unittest.TestCase):
    def test_skills_and_docs(self):
        self.assertEqual(check(), [])
