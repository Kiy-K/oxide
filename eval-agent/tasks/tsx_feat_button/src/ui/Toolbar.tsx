import { Button } from './Button';

/** Toolbar renders quick actions; disabled actions must not fire. */
export function Toolbar({ onSave }: { onSave: () => void }) {
  return (
    <div className="toolbar">
      <Button label="Save" onClick={onSave} />
      <Button label="Save as copy" onClick={onSave} disabled />
    </div>
  );
}
