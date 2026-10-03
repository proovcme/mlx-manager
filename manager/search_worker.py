"""Optional DDGS dependency runs outside the manager and model processes."""
import json
import sys
import time

from web_search import normalize_results


def main():
    try:
        from ddgs import DDGS
    except ImportError:
        print(json.dumps({'error': 'Установите зависимости веб-поиска: .venv/bin/python -m pip install -r manager/requirements-web.txt'}))
        return
    try:
        spec = json.loads(sys.stdin.read(4096))
        if not isinstance(spec['query'], str) or not 1 <= len(spec['query']) <= 500:
            raise ValueError('invalid query')
        for attempt in range(2):
            try:
                rows = DDGS(timeout=6).text(spec['query'], region='ru-ru', safesearch='moderate',
                                          max_results=8, backend=spec['backend'])
                results = normalize_results(rows)
                if results:
                    break
                raise ValueError('no usable results')
            except Exception:
                if attempt:
                    raise
                time.sleep(.5)
        # The parent's normalizer also checks all returned URLs and text bounds.
        print(json.dumps({'results': [{'title': r['title'], 'href': r['url'], 'body': r['snippet']} for r in results]}, ensure_ascii=False))
    except Exception:
        # Provider errors may contain URLs, queries or proxy credentials.
        print(json.dumps({'error': 'Поисковый сервис недоступен или не вернул результатов. Повторите запрос или отключите поиск.'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
