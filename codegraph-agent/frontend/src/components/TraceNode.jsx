import { Handle, Position } from "reactflow";

export default function TraceNode({ data, selected }) {
  const classes = [
    "trace-node",
    selected && "trace-node-selected",
    data.isStart && "trace-node-start",
    data.isEnd && "trace-node-end",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes}>
      <Handle type="target" position={Position.Top} />
      <div className="trace-node-order">{data.order}</div>
      <div className="trace-node-body">
        <div className="trace-node-name">{data.name}</div>
        <div className="trace-node-loc">
          {data.file_path}:{data.start_line}
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}