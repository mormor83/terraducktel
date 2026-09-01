import { createContext, useContext } from "react";

/**
 * The tree's active tag filter, shared with the leaf rows.
 *
 * A context rather than props: the only consumer is `WorkspaceLeafRow`, four
 * levels below `WorkspaceTree` (tree → account/subscription/project group →
 * folder body → leaf). Threading two props through three components that do
 * not otherwise care about tags is a lot of noise for one feature, and every
 * one of those signatures would need touching again for the next one.
 *
 * Default is inert, so a leaf rendered outside a provider (tests, Storybook)
 * shows static chips instead of throwing.
 */
export type ActiveTag = { key: string; value: string | null } | null;

export const TagFilterContext = createContext<{
  activeTag: ActiveTag;
  onTagClick?: (tagKey: string, value: string) => void;
}>({ activeTag: null });

export function useTagFilter() {
  return useContext(TagFilterContext);
}
