import unittest

from isrp.orchestration.errors import ValidationError
from isrp.orchestration.validation import validate_template


class TemplateValidationTests(unittest.TestCase):
    def test_rejects_cycle(self):
        with self.assertRaises(ValidationError):
            validate_template({
                "steps": [
                    {"key": "root", "type": "HUMAN_TASK"},
                    {"key": "start", "type": "HUMAN_TASK"},
                    {"key": "again", "type": "HUMAN_TASK"},
                    {"key": "end", "type": "END"},
                ],
                "transitions": [
                    {"from_step": "root", "to_step": "start"},
                    {"from_step": "start", "to_step": "again"},
                    {"from_step": "again", "to_step": "start"},
                    {"from_step": "again", "to_step": "end"},
                ],
            })

    def test_rejects_path_without_end(self):
        with self.assertRaises(ValidationError):
            validate_template({
                "steps": [
                    {"key": "start", "type": "FORK"},
                    {"key": "dead", "type": "HUMAN_TASK"},
                    {"key": "end", "type": "END"},
                ],
                "transitions": [
                    {"from_step": "start", "to_step": "dead"},
                    {"from_step": "start", "to_step": "end"},
                ],
            })

    def test_rejects_unreachable_step(self):
        with self.assertRaises(ValidationError):
            validate_template({
                "steps": [
                    {"key": "start", "type": "HUMAN_TASK"},
                    {"key": "orphan", "type": "HUMAN_TASK"},
                    {"key": "end", "type": "END"},
                ],
                "transitions": [{"from_step": "start", "to_step": "end"}],
            })

    def test_join_requires_rule(self):
        with self.assertRaises(ValidationError):
            validate_template({
                "steps": [{"key": "start", "type": "HUMAN_TASK"}, {"key": "join", "type": "JOIN"}, {"key": "end", "type": "END"}],
                "transitions": [{"from_step": "start", "to_step": "join"}, {"from_step": "join", "to_step": "end"}],
            })


if __name__ == "__main__":
    unittest.main()
