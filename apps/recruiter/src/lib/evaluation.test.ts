import { describe, expect, it } from "vitest";
import {
  CONFIDENCE,
  CRITERIA,
  DISCUSSION,
  depthRelation,
  depthSentence,
  dimensionLabel,
  interviewDepthLabel,
  stageIndex,
} from "./evaluation";
import completed from "../test/fixtures/evaluation.completed.json";

/**
 * The report's vocabulary.
 *
 * These are the words that stop two different measurements being read as one,
 * so they are pinned rather than left to whoever edits the JSX next.
 */

describe("the five criteria", () => {
  it("are the canonical five, in the contract's order", () => {
    expect(CRITERIA).toEqual([
      "Accuracy",
      "Depth",
      "Clarity",
      "Problem-Solving",
      "Communication",
    ]);
  });

  it("match the keys the API actually sends", () => {
    const row = completed.skill_assessment[0] as Record<string, unknown>;
    for (const criterion of CRITERIA) expect(row).toHaveProperty(criterion);
  });
});

describe("depth", () => {
  it("orders the ladder shallowest first", () => {
    expect(stageIndex("direct")).toBe(0);
    expect(stageIndex("probed")).toBe(1);
    expect(stageIndex("deep_probed")).toBe(2);
  });

  it("treats an unknown stage as the shallowest rather than throwing", () => {
    expect(stageIndex("extremely_probed")).toBe(0);
  });

  it.each([
    ["direct", "direct", "matched"],
    ["deep_probed", "deep_probed", "matched"],
    ["direct", "deep_probed", "exceeded"],
    ["deep_probed", "direct", "short_of"],
  ])("reads %s / %s as %s", (reached, demonstrated, relation) => {
    expect(depthRelation(reached, demonstrated)).toBe(relation);
  });

  it("never says that being probed more is a better result", () => {
    const sentences = [
      depthSentence("direct", "direct"),
      depthSentence("direct", "deep_probed"),
      depthSentence("deep_probed", "direct"),
      depthSentence("deep_probed", "deep_probed"),
    ].join(" ");
    expect(sentences).not.toMatch(/\b(better|worse|stronger|weaker|good|poor|failed)\b/i);
  });

  it("explains saturation as the answer arriving early, not as a shortfall", () => {
    expect(depthSentence("direct", "deep_probed")).toMatch(/no reason to keep asking/i);
  });

  it("explains over-probing as the evidence not reaching, not as a low score", () => {
    const sentence = depthSentence("deep_probed", "direct");
    expect(sentence).toMatch(/did not establish deeper capability/i);
    expect(sentence).not.toMatch(/score/i);
  });
});

describe("interview depth is a different idea from the probe ladder", () => {
  it.each([
    ["short", "Short"],
    ["medium", "Medium"],
    ["deep", "Deep"],
  ])("labels %s as %s", (value, label) => {
    expect(interviewDepthLabel(value)).toBe(label);
  });

  it("does not recognise an investigation stage as an interview depth", () => {
    expect(interviewDepthLabel("deep_probed")).toBe("");
    expect(interviewDepthLabel("direct")).toBe("");
    expect(interviewDepthLabel("probed")).toBe("");
  });
});

describe("discussion status", () => {
  it("never presents a coverage gap in the tone of a failure", () => {
    expect(DISCUSSION.not_discussed.tone).toBe("neutral");
    expect(DISCUSSION.not_discussed.meaning).toMatch(/coverage gap, not a weakness/i);
  });

  it("says a mention is about the interview, not the candidate", () => {
    expect(DISCUSSION.mentioned.meaning).toMatch(/more about the interview than about the candidate/i);
  });
});

describe("evidence confidence", () => {
  it("does not describe low confidence as low capability", () => {
    expect(CONFIDENCE.low.meaning).toMatch(/not about the candidate's ability/i);
    expect(CONFIDENCE.insufficient.meaning).toMatch(/do not know/i);
  });
});

describe("dimension labels", () => {
  it("names every dimension the API sent for this evaluation", () => {
    const dimensions = new Set<string>();
    for (const row of completed.skill_assessment) {
      for (const d of row.depth_evaluation.dimensions_demonstrated) dimensions.add(d);
      for (const d of row.depth_evaluation.dimensions_missing) dimensions.add(d);
    }
    expect(dimensions.size).toBeGreaterThan(0);
    for (const d of dimensions) {
      expect(dimensionLabel(d)).not.toContain("_");
    }
  });

  it("renders an unknown dimension rather than dropping it", () => {
    expect(dimensionLabel("systems_thinking")).toBe("Systems thinking");
  });
});

// --------------------------------------------------------------------------- //
//  All nine reached/demonstrated combinations, in one place
// --------------------------------------------------------------------------- //
describe("every combination of the two depth figures", () => {
  const STAGE_LIST = ["direct", "probed", "deep_probed"] as const;

  it("produces a sentence for all nine, and never equates probing with capability", () => {
    for (const reached of STAGE_LIST) {
      for (const demonstrated of STAGE_LIST) {
        const sentence = depthSentence(reached, demonstrated);
        expect(sentence.length).toBeGreaterThan(20);
        // The one thing the report must never say.
        expect(sentence).not.toMatch(/probed (?:more|twice) (?:means|shows|so)/i);
        expect(sentence).not.toMatch(/deeper investigation.*(?:better|stronger)/i);
      }
    }
  });

  it("calls the deep/deep case an agreement rather than an achievement", () => {
    // `deep_probed → deep_probed` is the combination no pilot transcript has
    // produced yet, so its wording is asserted here rather than in the browser.
    const sentence = depthSentence("deep_probed", "deep_probed");
    expect(sentence).toBe("The evidence reached the same depth the interview investigated.");
    expect(sentence).not.toMatch(/deep|advanced|strong/i);
  });

  it("distinguishes the two directions of disagreement", () => {
    expect(depthSentence("direct", "deep_probed")).toMatch(/went further than the interview/i);
    expect(depthSentence("deep_probed", "direct")).toMatch(/investigated further than the evidence/i);
    expect(depthSentence("probed", "probed")).toMatch(/same depth/i);
  });

  it("keeps the relation independent of which figure is larger", () => {
    expect(depthRelation("direct", "deep_probed")).toBe("exceeded");
    expect(depthRelation("deep_probed", "direct")).toBe("short_of");
    expect(depthRelation("probed", "probed")).toBe("matched");
    expect(depthRelation("deep_probed", "deep_probed")).toBe("matched");
  });
});
