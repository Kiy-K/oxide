import React from 'react';
import { Base } from './service';

export function App({ items }) {
  return (
    <div>
      <Panel label="p" />
      {items.map((i) => (
        <li key={i}>{i}</li>
      ))}
    </div>
  );
}

export class Panel extends React.Component {
  render() {
    return <button>{this.props.label}</button>;
  }
}

export default function mount() {
  return new Base(1);
}
