import { test } from 'bun:test';
import assert from 'node:assert';
import { render, screen, fireEvent } from '@testing-library/react';
import { Button } from '../src/ui/Button';
import { Toolbar } from '../src/ui/Toolbar';

test('clicking enabled button fires handler', () => {
  let clicks = 0;
  render(<Button label="Go" onClick={() => (clicks += 1)} />);
  fireEvent.click(screen.getByText('Go'));
  assert.equal(clicks, 1);
});

test('disabled button never fires and renders disabled attr', () => {
  let clicks = 0;
  render(<Button label="Nope" onClick={() => (clicks += 1)} disabled />);
  const el = screen.getByText('Nope') as HTMLButtonElement;
  assert.equal(el.disabled, true);
  fireEvent.click(el);
  assert.equal(clicks, 0);
});

test('toolbar disables the copy action', () => {
  render(<Toolbar onSave={() => {}} />);
  const copy = screen.getByText('Save as copy') as HTMLButtonElement;
  assert.equal(copy.disabled, true);
});
