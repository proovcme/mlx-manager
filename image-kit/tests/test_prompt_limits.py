import unittest
from types import SimpleNamespace
from mlx_image.prompt_limits import validate_prompt_tokens

class PromptLimitTests(unittest.TestCase):
    def test_counts_template_and_rejects_overflow_without_truncating(self):
        calls=[]
        def tokenize(text,**kw):calls.append((text,kw));return {'input_ids':text.split()}
        tok=SimpleNamespace(template='system {}',tokenizer=tokenize,max_length=3,add_special_tokens=True)
        self.assertEqual(validate_prompt_tokens(tok,'one two'),3)
        with self.assertRaisesRegex(ValueError,'4 tokens'):validate_prompt_tokens(tok,'one two three')
        self.assertFalse(calls[-1][1]['truncation'])
