import { createElement, type KeyboardEvent } from "react";
import { renderToString } from "react-dom/server";
import { expect, it, vi } from "vitest";
import type { ReactFlowProps } from "@xyflow/react";
import { spec } from "./workbench-fixtures";
let props: ReactFlowProps;
let controls: { showInteractive?: boolean };
vi.mock("@xyflow/react", () => ({
  ReactFlow: (value: ReactFlowProps) => {
    props = value;
    return createElement(
      "div",
      { "data-testid": "flow-contract" },
      value.children,
    );
  },
  Controls: (value: { showInteractive?: boolean }) => {
    controls = value;
    return null;
  },
  Background: () => null,
}));
it("locks every mutation control while retaining accessible rule selection", async () => {
  const { RuleGraph, selectFocusedGraphRule } =
    await import("../../src/features/research-workbench/rule-graph");
  const select = vi.fn();
  const html = renderToString(
    createElement(RuleGraph, {
      spec: spec.spec,
      selectedRule: "filter",
      onSelect: select,
    }),
  );
  expect(html).toContain("not an execution trace");
  expect(props.nodesDraggable).toBe(false);
  expect(props.nodesConnectable).toBe(false);
  expect(props.edgesReconnectable).toBe(false);
  expect(props.deleteKeyCode).toBeNull();
  expect(props.nodesFocusable).toBe(true);
  expect(controls.showInteractive).toBe(false);
  for (const node of props.nodes!) {
    expect(node.draggable).toBe(false);
    expect(node.connectable).toBe(false);
    expect(node.deletable).toBe(false);
    expect(node.focusable).toBe(true);
    expect(node.ariaLabel).toContain("Expected rule:");
  }
  for (const edge of props.edges!) {
    expect(edge.reconnectable).toBe(false);
    expect(edge.deletable).toBe(false);
  }
  props.onNodesChange!([{ type: "select", id: "rank", selected: true }]);
  expect(select).toHaveBeenCalledExactlyOnceWith("rank");
  props.onNodesChange!([
    { type: "position", id: "rank", position: { x: 999, y: 999 } },
    { type: "remove", id: "filter" },
  ]);
  expect(select).toHaveBeenCalledTimes(1);
  expect(props.nodes![1].position).toEqual({ x: 260, y: 0 });
  // Exercise the application's actual capture handler, not ReactFlow's mock
  // selection callback. No browser/DOM execution is claimed by this unit test.
  class ElementDouble {
    constructor(
      readonly id: string,
      readonly node = true,
    ) {}
    matches(selector: string) {
      return selector === ".react-flow__node" && this.node;
    }
    getAttribute(name: string) {
      return name === "data-id" ? this.id : null;
    }
  }
  vi.stubGlobal("HTMLElement", ElementDouble);
  try {
    const target = new ElementDouble("filter");
    const currentTarget = {
      ownerDocument: { activeElement: target as unknown },
      contains: (value: unknown) => value === target,
    };
    const preventDefault = vi.fn(),
      stopPropagation = vi.fn();
    const event = {
      key: "Enter",
      target,
      currentTarget,
      preventDefault,
      stopPropagation,
    };
    const dispatch = (patch: object = {}) =>
      selectFocusedGraphRule(
        { ...event, ...patch } as unknown as KeyboardEvent<HTMLDivElement>,
        spec.spec.rules,
        select,
      );
    select.mockClear();
    dispatch();
    dispatch({ key: " " });
    dispatch(); // already selected: Enter still explicitly inspects the same rule
    expect(select.mock.calls).toEqual([["filter"], ["filter"], ["filter"]]);
    expect(preventDefault).toHaveBeenCalledTimes(3);
    expect(stopPropagation).toHaveBeenCalledTimes(3);
    for (const key of ["Delete", "Backspace", "ArrowRight", "Escape"])
      dispatch({ key });
    for (const modifier of [
      "altKey",
      "ctrlKey",
      "metaKey",
      "shiftKey",
      "repeat",
    ])
      dispatch({ [modifier]: true });
    for (const other of [
      new ElementDouble("unknown"),
      new ElementDouble("filter", false),
    ]) {
      currentTarget.ownerDocument.activeElement = other;
      dispatch({
        target: other,
        currentTarget: { ...currentTarget, contains: () => true },
      });
    }
    currentTarget.ownerDocument.activeElement = null;
    dispatch(); // unfocused target
    currentTarget.ownerDocument.activeElement = target;
    dispatch({ currentTarget: { ...currentTarget, contains: () => false } });
    expect(select).toHaveBeenCalledTimes(3);
    expect(preventDefault).toHaveBeenCalledTimes(3);
    expect(stopPropagation).toHaveBeenCalledTimes(3);
  } finally {
    vi.unstubAllGlobals();
  }
});
