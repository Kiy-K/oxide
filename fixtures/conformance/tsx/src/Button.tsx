import * as React from 'react';

export interface ButtonProps {
  label: string;
}

export function Button(props: ButtonProps) {
  return <button>{props.label}</button>;
}

export class Panel extends React.Component<ButtonProps> {
  render() {
    return <Button label={this.props.label} />;
  }
}
