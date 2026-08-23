import { render, screen, fireEvent } from '@testing-library/react';
import { Button } from '../Button';

describe('Button', () => {
  it('invokes onClick when clicked', () => {
    let clicks = 0;
    render(<Button label="Save" onClick={() => (clicks += 1)} />);
    fireEvent.click(screen.getByText('Save'));
    expect(clicks).toBe(1);
  });

  it('does not fire when disabled', () => {
    const onClick = jest.fn();
    render(<Button label="Save" onClick={onClick} disabled />);
    fireEvent.click(screen.getByText('Save'));
    expect(onClick).not.toHaveBeenCalled();
  });
});
