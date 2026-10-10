"""Reject overlong prompts before the encoder silently truncates them."""

def validate_prompt_tokens(tokenizer, prompt):
    template = getattr(tokenizer, 'template', None)
    formatted = template.format(prompt) if template else prompt
    ids = tokenizer.tokenizer(formatted, truncation=False, padding=False,
        add_special_tokens=getattr(tokenizer, 'add_special_tokens', True))['input_ids']
    limit = tokenizer.max_length
    if len(ids) > limit:
        raise ValueError(f'Image prompt uses {len(ids)} tokens including the system template; limit is {limit}. Shorten the prompt; it has not been truncated.')
    return len(ids)
