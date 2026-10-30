import { describe, expect, it } from "vitest";
import { profitLanguage, tierEmptyMessage } from "./format";

describe("prediction center copy", () => {
  it("keeps expected profit distinct from potential profit", () => {
    expect(profitLanguage.expected.toLowerCase()).toContain("average");
    expect(profitLanguage.potential.toLowerCase()).toContain("if it wins");
    expect(profitLanguage.expected).not.toEqual(profitLanguage.potential);
  });

  it("explains an empty tier instead of inventing a pick", () => {
    const message = tierEmptyMessage("Conservative");
    expect(message).toContain("No wager currently meets");
    expect(message.toLowerCase()).toContain("high win probability is not enough");
  });
});
