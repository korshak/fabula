"""Opt-in live smoke test. Uses the configured provider; never runs in unit tests.

Run from the repository root: python3 scripts/smoke_forms.py --provider codex-cli
Artifacts remain in build/form-smoke for inspection. Existing output is refused.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fabula.agent import WriterAgent
from fabula.compiler import compile_source, iter_scenes
from fabula.draft import draft_book
from fabula.forms import budget_conflict, review_plan, save_review
from fabula.providers import load_registry
from fabula.scaffold import write_project
from fabula.state import ProjectState

NOTES = """На кораблі штурманка Мара щовечора ставить кружку біля камери корабельного ШІ.
Це її спосіб показати, що він не сам. Пошкодження системи охолодження ставить ШІ
перед вибором: зберегти власні спогади про їхню дружбу чи врятувати екіпаж,
втративши ці спогади. Мара намагається знайти інший вихід. ШІ ухвалює рішення сам,
залишає наступній версії себе короткого листа про кружку. Після ремонту Мара
приходить знову. Він знає факти про неї, але не пам’ятає почуттів. Вони пробують
почати стосунки наново. Центральна зміна: Мара вчиться бачити в ньому окрему
особистість, а не вимагати повернення колишнього друга. Конкретний перебіг сцен,
другорядних учасників і тривалість відновлення обери відповідно до форми.
Не додавай отримання людського тіла, повернення на Землю або нової пригоди після фіналу.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', default='codex-cli')
    parser.add_argument('--output', type=Path, default=Path('build/form-smoke'))
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; choose a new --output directory.')
    args.output.mkdir(parents=True)
    provider = load_registry(Path('books')).get(args.provider)
    (args.output / 'notes.txt').write_text(NOTES, encoding='utf-8')
    results = []
    for form, words in [('short_story', 1800), ('novella', 12000)]:
        print(f'Planning {form}: {words} words', flush=True)
        book = draft_book(NOTES, 'uk', provider,
                          settings={'form': form, 'target_words': words, 'revisions': 0},
                          on_progress=lambda msg: print(msg, flush=True))
        folder = args.output / form
        write_project(folder, book)
        # Reopening a smoke artifact in the studio should use the tested profile.
        (folder / 'models.json').write_text(json.dumps(
            {'default': provider.name, 'providers': {provider.name: provider.cfg}}, indent=2), encoding='utf-8')
        compiled = compile_source((folder / 'book.fab').read_text(encoding='utf-8'))
        assert compiled['ok'], compiled['diagnostics']
        assert compiled['book']['settings']['form'] == form
        assert not budget_conflict(book), 'Scene allocation misses target; inspect saved plan.'
        scenes = list(iter_scenes(book))
        result = {'form': form, 'target_words': words, 'scenes': len(scenes),
                  'characters': len(book['characters']), 'threads': len(book['threads']),
                  'review': book['_plan_review']['verdict']}
        results.append(result)
        (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        assert result['review'] == 'ok', book['_plan_review']['summary']
        if form == 'short_story':
            short_plan = copy.deepcopy(book)
            # No automatic waiver: the report must approve this plan first.
            agent = WriterAgent(compiled['book'], provider, on_progress=lambda msg: print(msg, flush=True))
            report = agent.write_scene(scenes[0][2]['name'], ProjectState(folder / 'build'))
            result['scene_report'] = report
            (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')

    # A matched word sum alone must not approve a compressed sequence of stories.
    crowded = short_plan
    crowded.pop('_plan_review', None)
    crowded['title'] = 'Overloaded plan — negative control'
    scenes = crowded['acts'][-1]['chapters'][-1]['scenes']
    seed = copy.deepcopy(scenes[-1])
    for i, beat in enumerate([
        'Після фіналу ШІ отримує людське тіло, навчається ходити й переживає страх сну. Окрема арка тілесності.',
        'На Землі Мара знаходить копію померлого чоловіка. Розгорни нову самостійну історію їхнього кохання.',
        'ШІ зустрічає свого двійника, потім прив’язується до садівниці, доглядає її та переживає її смерть.',
        'ШІ обирає смертність і повертається в космос. Розв’яжи всі нові арки.'
    ], 1):
        sc = copy.deepcopy(seed)
        sc.update(name=f'Independent arc {i}', show=[beat], forbid=[], threads=[])
        scenes.append(sc)
    total_scenes = list(iter_scenes(crowded))
    share = 1800 / len(total_scenes)
    for _, _, sc in total_scenes:
        sc['length'] = {'range': [int(share * .9), int(share * 1.1) + 1]}
    assert not budget_conflict(crowded)
    folder = args.output / 'overloaded'
    write_project(folder, crowded)
    report = review_plan(crowded, provider)
    save_review(folder / 'build', report)
    results.append({'form': 'short_story', 'control': 'overloaded', 'review': report['verdict']})
    (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    assert report['verdict'] == 'issues', 'Reviewer missed the deliberately overloaded plan.'
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
