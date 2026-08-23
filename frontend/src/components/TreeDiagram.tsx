import type { CurveData, TreeNode } from "../types";

const WIDTH = 640;
const ROW_HEIGHT = 56;
const PAD_TOP = 20;
const PAD_SIDE = 20;
const PAD_BOTTOM = 20;
const MAX_DEPTH = 3; // matches curve_model.py's XGBRegressor(max_depth=3)

interface LaidOutNode {
  node: TreeNode;
  x: number;
  y: number;
  depth: number;
}

function countLeaves(node: TreeNode): number {
  return node.type === "leaf" ? 1 : countLeaves(node.left) + countLeaves(node.right);
}

/** Assigns each node an x based on how many leaves sit under it (so sibling
 * subtrees never overlap regardless of how lopsided the tree is) and a y
 * from its actual depth -- a branch that stops early renders visibly
 * higher up rather than being stretched to fake a uniform depth. */
function layout(node: TreeNode, depth: number, xStart: number, xEnd: number, out: LaidOutNode[]): number {
  const x = (xStart + xEnd) / 2;
  const y = PAD_TOP + depth * ROW_HEIGHT;
  out.push({ node, x, y, depth });
  if (node.type === "leaf") return x;

  const leftLeaves = countLeaves(node.left);
  const totalLeaves = leftLeaves + countLeaves(node.right);
  const split = xStart + ((xEnd - xStart) * leftLeaves) / totalLeaves;
  layout(node.left, depth + 1, xStart, split, out);
  layout(node.right, depth + 1, split, xEnd, out);
  return x;
}

function edges(node: TreeNode, laid: Map<TreeNode, LaidOutNode>, out: [LaidOutNode, LaidOutNode][]): void {
  if (node.type === "leaf") return;
  const parent = laid.get(node)!;
  out.push([parent, laid.get(node.left)!]);
  out.push([parent, laid.get(node.right)!]);
  edges(node.left, laid, out);
  edges(node.right, laid, out);
}

/** Tree #0 from a skin's fitted XGBoost model -- the one tree in the
 * ensemble that's actually meaningful to look at alone, since it's fit
 * close to the raw signal while every tree after it patches an
 * ever-smaller residual. Leaf values are the model's raw log-space
 * contribution, NOT a dollar price -- only the sum of all 150 trees,
 * converted back with expm1, is an actual price (see curve_model.py). */
export function TreeDiagram({ data }: { data: CurveData }) {
  if (data.model_type !== "xgboost" || data.first_tree === null) {
    const reason =
      data.model_type === "knn"
        ? "this skin uses the KNN fallback (not enough data yet for XGBoost), so there's no tree to show."
        : "not enough real data collected for this skin yet to fit a model at all.";
    return <p className="subtitle">No tree to show — {reason}</p>;
  }

  const leafCount = countLeaves(data.first_tree);
  const laidList: LaidOutNode[] = [];
  layout(data.first_tree, 0, PAD_SIDE, WIDTH - PAD_SIDE, laidList);
  const laid = new Map(laidList.map((l) => [l.node, l]));
  const edgeList: [LaidOutNode, LaidOutNode][] = [];
  edges(data.first_tree, laid, edgeList);

  const height = PAD_TOP + MAX_DEPTH * ROW_HEIGHT + 20 + PAD_BOTTOM;

  return (
    <div className="tree-diagram-wrap">
      <svg
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label={`The first tree (of 150) in ${data.skin_name}'s fitted model, with ${leafCount} leaves, showing the float thresholds it splits on.`}
        className="tree-diagram"
      >
        {edgeList.map(([parent, child], i) => (
          <line key={i} x1={parent.x} y1={parent.y + 14} x2={child.x} y2={child.y - 14} className="tree-edge" />
        ))}

        {laidList.map((l, i) =>
          l.node.type === "split" ? (
            <g key={i}>
              <rect x={l.x - 46} y={l.y - 14} width={92} height={28} rx={5} className="tree-split-box" />
              <text x={l.x} y={l.y + 4} textAnchor="middle" className="tree-split-label">
                float &lt; {l.node.threshold.toFixed(3)}
              </text>
            </g>
          ) : (
            <g key={i}>
              <rect x={l.x - 34} y={l.y - 14} width={68} height={28} rx={14} className="tree-leaf-box" />
              <text x={l.x} y={l.y + 4} textAnchor="middle" className="tree-leaf-label">
                {l.node.value >= 0 ? "+" : ""}
                {l.node.value.toFixed(3)}
              </text>
            </g>
          )
        )}
      </svg>
      <p className="subtitle tree-diagram-caption">
        Tree 1 of 150 from {data.skin_name}&rsquo;s fitted model. Leaf values are the model&rsquo;s raw log-price
        correction from this one tree, not a dollar amount &mdash; the actual prediction sums all 150 trees&rsquo;
        corrections together, then converts back to dollars.
      </p>
    </div>
  );
}
