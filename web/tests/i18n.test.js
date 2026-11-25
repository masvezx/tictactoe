import assert from 'node:assert/strict';
import test from 'node:test';
import { apiErrorKey, languages, translate, translations } from '../src/i18n.js';

test('all languages translate the full interface, rules and language menu', () => {
  const reference = translations.ru;
  for (const language of languages) {
    const dictionary = translations[language];
    assert.deepEqual(Object.keys(dictionary).sort(), Object.keys(reference).sort(), language);
    assert.deepEqual(Object.keys(dictionary.languageNames).sort(), [...languages].sort());
    assert.equal(dictionary.rulesList.length, reference.rulesList.length);
    for (const [key, value] of Object.entries(dictionary)) {
      if (typeof value !== 'string') continue;
      assert.ok(value.trim(), `${language}.${key} is empty`);
      assert.deepEqual(value.match(/\{\w+\}/g), reference[key].match(/\{\w+\}/g), `${language}.${key}`);
    }
    for (const rule of dictionary.rulesList) assert.ok(rule.trim());
  }
});

test('cell labels interpolate coordinates and use the chosen language', () => {
  for (const language of languages) {
    const label = translate(language, 'cell', { row: 2, column: 3, value: translate(language, 'empty') });
    assert.ok(label.includes('2') && label.includes('3'));
    assert.ok(label.includes(translations[language].empty));
    assert.ok(!label.includes('{') && !label.includes('undefined'));
  }
});

test('the same error can be rendered again in a different language', () => {
  for (const status of [401, 404, 409, 422, 503, 500]) {
    const key = apiErrorKey(status);
    const messages = languages.map(language => translate(language, key, { status }));
    assert.equal(new Set(messages).size, languages.length);
    for (const message of messages) assert.ok(!message.includes('undefined'));
    if (status === 500) assert.ok(messages.every(message => message.includes('500')));
  }
});
