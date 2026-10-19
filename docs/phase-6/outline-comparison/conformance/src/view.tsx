import React, { useState } from "react";

type Props = { title: string };

export function Panel({ title }: Props) {
  const [open, setOpen] = useState(false);
  return <div className="panel" onClick={() => setOpen(!open)}>{title}</div>;
}

export const Badge = ({ n }: { n: number }) => <span>{n}</span>;

export default Panel;
