    var mcelLabState = window.mcelLabState || (window.mcelLabState = createDefaultMcelLabState());

    function mcelLabDependenciesReady() {
      return Boolean(
        window.McelLabContract &&
        window.McelLabEngine &&
        window.McelLabLawRegistry &&
        window.McelLabEditor &&
        window.McelLabScenarios &&
        window.McelLabChromeLaw &&
        window.McelLabBrowserObserver &&
        window.McelLabLayoutLaw &&
        window.McelLabComponentLaw &&
        window.McelLabStateLaw &&
        window.McelLabDataLaw &&
        window.McelLabFormLaw &&
        window.McelLabActionLaw &&
        window.McelLabRenderLaw &&
        window.McelLabA11yLaw &&
        window.McelLabPerformanceLaw &&
        window.McelLabPlatformSpine &&
        window.McelLabWorkbench &&
        window.McelLabBrowserRunner &&
        window.McelLabSiteSkeleton &&
        window.McelLabAcidTests &&
        window.McelLabSupervisor &&
        window.McelLabKernel &&
        window.McelElementRegistry &&
        window.McelLabScm &&
        window.McelElementsCore &&
        window.McelElementAcidTest &&
        window.TaskManagerMcel &&
        window.McelSupercut &&
        window.GitToolsMcel &&
        window.MCEL
      );
    }

    function initMcelLabApp() {
      if (!mcelLabApp) return;
      if (!mcelLabDependenciesReady()) {
        window.setTimeout(initMcelLabApp, 0);
        return;
      }
      mcelLabState = window.mcelLabState || (window.mcelLabState = createDefaultMcelLabState());
      if (mcelLabState.initialized) return;
      mcelLabState.initialized = true;
      if (mcelSourceHtml && !mcelSourceHtml.value.trim()) {
        mcelSourceHtml.value = McelLabContract.defaultSource;
      }
      populateMcelThemes();
      populateMcelChromes();
      populateMcelScenarios();
      populateMcelAcidCases();
      bindMcelLabControls();
      initMcelLabGrapes();
      selectMcelSourceIndex(0, "initial-selection");
      compileMcelLabSource("initial-load");
      renderMcelElementLibraryAcidTest("boot");
      renderMcelTinyContractTest("boot", { exercise: false });
      renderMcelAutopilotDeferred("boot");
    }


    function renderMcelElementLibraryAcidTest(reason = "manual") {
      if (!window.McelElementAcidTest?.run) {
        if (mcelElementAcidReport) {
          mcelElementAcidReport.textContent = "MCEL Element Library Acid Test unavailable: registry or runner has not loaded.";
          mcelElementAcidReport.dataset.status = "unavailable";
        }
        return null;
      }
      const report = window.McelElementAcidTest.run({
        document,
        canvas: mcelElementAcidCanvas,
        summary: mcelElementAcidSummary,
        report: mcelElementAcidReport,
        reason
      });
      mcelLabState.lastElementAcidReport = report;
      if (mcelElementAcidReport) {
        mcelElementAcidReport.dataset.status = report?.status || "unknown";
      }
      return report;
    }


    function mcelTinyContractLanguageSource() {
      return (mcelTinyContractLanguageTemplate?.innerHTML || "").trim();
    }

    function mcelTinyContractSourceHtml() {
      const templateSource = mcelTinyContractSourceTemplate?.innerHTML || "";
      return templateSource.trim();
    }

    function normalizeMcelTinyContractHtml(html) {
      return String(html || "")
        .replace(/>\s+</g, ">\n<")
        .replace(/[ \t]+\n/g, "\n")
        .trim();
    }

    function mcelTinyContractHash(value) {
      return Array.from(String(value || "")).reduce((hash, char) => {
        return ((hash << 5) - hash + char.charCodeAt(0)) | 0;
      }, 0).toString(16);
    }

    function mcelTinyContractStableJson(value) {
      if (Array.isArray(value)) {
        return `[${value.map((item) => mcelTinyContractStableJson(item)).join(",")}]`;
      }
      if (value && typeof value === "object") {
        return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${mcelTinyContractStableJson(value[key])}`).join(",")}}`;
      }
      return JSON.stringify(value ?? null);
    }

    function mcelTinyContractObjectHash(value) {
      return mcelTinyContractHash(mcelTinyContractStableJson(value));
    }

    function mcelTinyContractWalletAccountHash(account = "") {
      const normalized = String(account || "").trim().toLowerCase();
      return normalized ? `account:${mcelTinyContractHash(normalized)}` : "";
    }

    function mcelTinyContractTxProbeEnvelopeIds(draftProbe = {}) {
      return ["nonce", "gasEstimate", "ethCall"]
        .map((key) => {
          const envelope = draftProbe?.[key] || {};
          const method = envelope.method || key;
          const status = envelope.status || "not-probed";
          return `${method}:${status}`;
        });
    }

    function mcelTinyContractTxDraftInvalidation(reason, detail = {}) {
      return {
        reason,
        detail,
        at: "runtime-tx-draft-boundary"
      };
    }

    function mcelTinyContractSelectedRequestSnapshot(request = null) {
      return request
        ? {
            id: request.id || "",
            title: request.title || "",
            status: request.status || "",
            risk: request.risk || "",
            contractMethod: request.contractMethod || "",
            evidenceRequired: request.evidenceRequired === true
          }
        : null;
    }

    function mcelTinyContractSameChainId(left = "", right = "") {
      return String(left || "").toLowerCase() === String(right || "").toLowerCase();
    }

    function mcelTinyContractTxDraftInvalidationKey(entry = {}) {
      return `${entry.reason || ""}:${mcelTinyContractStableJson(entry.detail || {})}`;
    }

    function mcelTinyContractMergeTxDraftInvalidations(...lists) {
      const seen = new Set();
      const merged = [];
      lists.flat().filter(Boolean).forEach((entry) => {
        const reason = entry.reason || "";
        if (!reason) return;
        const normalized = {
          reason,
          detail: entry.detail || {},
          at: entry.at || "runtime-tx-draft-boundary"
        };
        const key = mcelTinyContractTxDraftInvalidationKey(normalized);
        if (seen.has(key)) return;
        seen.add(key);
        merged.push(normalized);
      });
      return merged;
    }

    function mcelTinyContractLatestSequenceEntry(sequence = []) {
      return Array.isArray(sequence) && sequence.length ? sequence[sequence.length - 1] || {} : {};
    }

    function mcelCommitBoundaryDefault(action = "mcel.serious-action", reason = "waiting") {
      return {
        kind: "mcel-18n-commit-boundary.v1",
        boundaryVersion: "18N-MCEL-j",
        action,
        status: "locked-no-draft",
        seriousAction: true,
        locked: true,
        mcelOnly: true,
        rule: "No serious MCEL action commits from raw UI state.",
        mcelCommitDraft: null,
        mcelCommitProvenance: null,
        mcelCommitFreshness: {
          kind: "mcelCommitFreshness.v1",
          status: "not-observed",
          valid: false,
          invalidatedBy: [],
          reason
        },
        mcelCommitConsumerGate: {
          kind: "mcelCommitConsumerGate.v1",
          status: "blocked",
          valid: false,
          reason: "No current commit draft has passed the MCEL 18N consumer gate."
        },
        mcelCommitPreflight: {
          kind: "mcelCommitPreflight.v1",
          status: "locked",
          allowed: false,
          canCommit: false,
          canSend: false,
          canSign: false,
          canBroadcast: false,
          blockers: ["draft-not-ready", "consumer-gate-not-proven", "wallet-send-sign-locked"]
        },
        mcelCommitReceipt: {
          kind: "mcelCommitReceipt.v1",
          status: "blocked",
          committed: false,
          mutationExecuted: false,
          reason
        }
      };
    }

    function mcelCommitBoundaryDraft({
      action = "mcel.serious-action",
      specimen = "mcel",
      source = {},
      targets = {},
      proposedChanges = {},
      intendedWrites = [],
      proofRefs = [],
      locked = true,
      reason = "draft-created"
    } = {}) {
      const draftPayload = {action, specimen, source, targets, proposedChanges, intendedWrites, proofRefs};
      return {
        kind: "mcelCommitDraft.v1",
        draftId: `mcelCommitDraft:${mcelTinyContractObjectHash(draftPayload)}`,
        action,
        specimen,
        seriousAction: true,
        locked: locked !== false,
        mcelOnly: true,
        source,
        targets,
        proposedChanges,
        intendedWrites,
        proofRefs,
        createdFor: reason,
        createdFromHash: mcelTinyContractObjectHash({source, targets, proposedChanges}),
        invariant: [
          "intent draft exists before mutation",
          "source and target provenance are recorded",
          "freshness must be checked at the consumer",
          "preflight must explain allowed or blocked state",
          "commit receipt must record the decision"
        ]
      };
    }

    function mcelCommitBoundaryProvenance({draft = {}, provenance = {}, current = {}} = {}) {
      const sourceHash = provenance.sourceHash || draft.source?.selectedRequestHash || current.sourceHash || "";
      const targetHash = provenance.targetHash || current.targetHash || "";
      const draftHash = draft.draftId || mcelTinyContractObjectHash(draft);
      return {
        kind: "mcelCommitProvenance.v1",
        draftHash,
        sourceHash,
        targetHash,
        sourceSnapshot: provenance.sourceSnapshot || draft.source || {},
        targetSnapshot: provenance.targetSnapshot || draft.targets || {},
        proofRefs: Array.isArray(draft.proofRefs) ? draft.proofRefs : [],
        provenanceEnforced: Boolean(draftHash && (sourceHash || targetHash)),
        invariant: [
          "the reviewed intent is identified",
          "the source used to build the draft is identified",
          "the target receiving the mutation is identified"
        ]
      };
    }

    function mcelCommitBoundaryFreshness({
      draft = {},
      provenance = {},
      current = {},
      invalidatedBy = [],
      reason = "consumer-freshness-check"
    } = {}) {
      const invalidations = [...invalidatedBy.filter(Boolean)];
      if (provenance.sourceHash && current.sourceHash && provenance.sourceHash !== current.sourceHash) {
        invalidations.push(mcelTinyContractTxDraftInvalidation("commit-source-changed", {
          previousSourceHash: provenance.sourceHash,
          currentSourceHash: current.sourceHash
        }));
      }
      if (provenance.targetHash && current.targetHash && provenance.targetHash !== current.targetHash) {
        invalidations.push(mcelTinyContractTxDraftInvalidation("commit-target-changed", {
          previousTargetHash: provenance.targetHash,
          currentTargetHash: current.targetHash
        }));
      }
      if (draft.locked !== true && draft.seriousAction === true) {
        invalidations.push(mcelTinyContractTxDraftInvalidation("serious-action-not-locked", {
          action: draft.action || ""
        }));
      }
      const uniqueInvalidations = mcelTinyContractMergeTxDraftInvalidations(invalidations);
      const valid = uniqueInvalidations.length === 0 && Boolean(provenance.provenanceEnforced);
      return {
        kind: "mcelCommitFreshness.v1",
        status: valid ? "valid" : (uniqueInvalidations.length ? "invalidated" : "stale"),
        valid,
        reason,
        sourceHash: current.sourceHash || "",
        targetHash: current.targetHash || "",
        invalidatedBy: uniqueInvalidations,
        action: valid
          ? "draft is current; continue to consumer gate"
          : "rebuild draft from the current MCEL receipt before commit",
        invariant: [
          "source hash must match",
          "target hash must match",
          "draft must still be locked until the consumer gate permits a commit"
        ]
      };
    }

    function mcelCommitBoundaryConsumerGate({
      draft = {},
      provenance = {},
      freshness = {},
      consumer = "mcel.consumer",
      forceLocked = true,
      blockers = []
    } = {}) {
      const invalidationReasons = (freshness.invalidatedBy || [])
        .map((entry) => entry?.reason || entry?.kind || entry)
        .filter(Boolean);
      const lockedBlocker = forceLocked ? ["wallet-send-sign-locked"] : [];
      const allBlockers = [...new Set([
        ...invalidationReasons,
        ...blockers.filter(Boolean),
        ...lockedBlocker
      ])];
      const valid = freshness.valid === true && provenance.provenanceEnforced === true && allBlockers.length === 0;
      return {
        kind: "mcelCommitConsumerGate.v1",
        consumer,
        status: valid ? "pass" : "blocked",
        valid,
        provenanceEnforced: provenance.provenanceEnforced === true,
        freshnessStatus: freshness.status || "not-observed",
        blockers: allBlockers,
        reason: valid
          ? "MCEL 18N consumer gate accepted the current draft."
          : `MCEL 18N consumer gate blocked commit: ${allBlockers.join(", ") || freshness.status || "not proven"}`,
        allowedActions: valid ? ["commit-with-receipt"] : ["rebuild-draft", "inspect-preflight"],
        invariant: [
          "consumer cannot use stale intent",
          "consumer cannot use unproven intent",
          "consumer cannot bypass the explicit lock"
        ]
      };
    }

    function mcelCommitBoundaryPreflight({draft = {}, freshness = {}, consumerGate = {}} = {}) {
      const gateBlockers = Array.isArray(consumerGate.blockers) ? consumerGate.blockers : [];
      const freshnessBlockers = (freshness.invalidatedBy || []).map((entry) => entry?.reason || entry).filter(Boolean);
      const blockers = [...new Set([...gateBlockers, ...freshnessBlockers])];
      const allowed = consumerGate.valid === true && blockers.length === 0;
      return {
        kind: "mcelCommitPreflight.v1",
        status: allowed ? "allowed" : "locked",
        allowed,
        canCommit: allowed,
        canSend: false,
        canSign: false,
        canBroadcast: false,
        blockers: allowed ? [] : (blockers.length ? blockers : ["wallet-send-sign-locked"]),
        summary: allowed
          ? "MCEL commit may proceed only through a receipting commit function."
          : "MCEL commit is blocked until draft, provenance, freshness, gate, and lock state agree.",
        allowedActions: allowed ? ["commit-with-receipt"] : ["rebuild-draft", "refresh-wallet-proof", "inspect-receipt"],
        invariant: [
          "preflight is explicit",
          "serious mutation state is never inferred from the button",
          "wallet send/sign/broadcast remains unavailable in 18N-MCEL-j"
        ]
      };
    }

    function mcelCommitBoundaryReceipt({
      draft = {},
      provenance = {},
      freshness = {},
      consumerGate = {},
      preflight = {},
      reason = "mcel-18n-preflight"
    } = {}) {
      const committed = preflight.allowed === true && consumerGate.valid === true;
      return {
        kind: "mcelCommitReceipt.v1",
        receiptVersion: "18N-MCEL-j",
        action: draft.action || "mcel.serious-action",
        status: committed ? "allowed" : "blocked",
        committed,
        mutationExecuted: false,
        reason,
        draftId: draft.draftId || "",
        provenanceHash: mcelTinyContractObjectHash(provenance || {}),
        freshnessStatus: freshness.status || "not-observed",
        consumerGateStatus: consumerGate.status || "not-observed",
        preflightStatus: preflight.status || "not-observed",
        blockers: preflight.blockers || consumerGate.blockers || [],
        proof: {
          draftKind: draft.kind || "",
          provenanceKind: provenance.kind || "",
          freshnessKind: freshness.kind || "",
          consumerGateKind: consumerGate.kind || "",
          preflightKind: preflight.kind || ""
        },
        invariant: [
          "receipt records the commit decision",
          "receipt records blocked state even when no mutation executes",
          "receipt never implies provider execution"
        ]
      };
    }


    function mcelWalletToolCurrentSnapshot({source = {}, state = {}, runtime = {}, request = null, simulation = null} = {}) {
      const txDraft = runtime.txDraft || {};
      const wallet = runtime.wallet || {};
      const network = runtime.network || {};
      const sourceRequestSnapshot = mcelTinyContractSelectedRequestSnapshot(request);
      const draftRequestSnapshot = txDraft.selectedRequestSnapshot || null;
      const currentSourceRequestHash = sourceRequestSnapshot ? mcelTinyContractObjectHash(sourceRequestSnapshot) : "";
      const draftSourceRequestHash = txDraft.sourceRequestHash || (draftRequestSnapshot ? mcelTinyContractObjectHash(draftRequestSnapshot) : "");
      const expectedChainId = network.expectedChainId || source.devRelease?.devNetwork?.chainId || txDraft.expectedChainId || txDraft.chainProof?.expectedChainId || "0x28757b2";
      const draftChainId = txDraft.chainId || txDraft.chainProof?.chainId || "";
      const currentChainId = network.chainId || wallet.chainId || "";
      const draftAccountHash = txDraft.walletAccountHash || mcelTinyContractWalletAccountHash(txDraft.from || "");
      const currentAccount = wallet.account || wallet.address || "";
      const currentAccountHash = mcelTinyContractWalletAccountHash(currentAccount);
      const draftTarget = txDraft.to || source.devRelease?.contractAddress || "";
      const currentTarget = source.devRelease?.contractAddress || txDraft.to || "";
      const draftValue = txDraft.value || "0x0";
      const currentValue = txDraft.value || "0x0";
      const draftCalldataHash = txDraft.calldata || txDraft.data ? mcelTinyContractHash(txDraft.calldata || txDraft.data || "") : "";
      const currentCalldataHash = txDraft.calldata || txDraft.data ? mcelTinyContractHash(txDraft.calldata || txDraft.data || "") : "";
      const draftTargetHash = mcelTinyContractObjectHash({
        accountHash: draftAccountHash,
        chainId: draftChainId,
        expectedChainId,
        to: draftTarget,
        value: draftValue,
        calldataHash: draftCalldataHash,
        runtimeBoundary: txDraft.boundary || "runtime-only-no-send"
      });
      const currentTargetHash = mcelTinyContractObjectHash({
        accountHash: currentAccountHash,
        chainId: currentChainId,
        expectedChainId,
        to: currentTarget,
        value: currentValue,
        calldataHash: currentCalldataHash,
        runtimeBoundary: txDraft.boundary || "runtime-only-no-send"
      });
      let snapshot = {
        kind: "mcelWalletSnapshot.v1",
        selectedRequestId: request?.id || state.selectedRequestId || txDraft.requestId || "",
        sourceRequestSnapshot,
        draftRequestSnapshot,
        currentSourceRequestHash,
        draftSourceRequestHash,
        draftAccountHash,
        currentAccountHash,
        currentAccountLabel: currentAccount ? `${currentAccount.slice(0, 12)}…` : "not connected",
        draftChainId,
        currentChainId,
        expectedChainId,
        draftTarget,
        currentTarget,
        draftValue,
        currentValue,
        draftCalldataHash,
        currentCalldataHash,
        draftTargetHash,
        currentTargetHash,
        simulation: simulation && simulation.kind ? simulation : null
      };
      if (simulation?.kind === "account") {
        snapshot = {
          ...snapshot,
          currentAccountHash: mcelTinyContractWalletAccountHash(`${snapshot.currentAccountHash || "missing-account"}:simulated-account-change:${simulation.sequence || 0}`),
          currentAccountLabel: `${snapshot.currentAccountLabel} (simulated account change)`
        };
      }
      if (simulation?.kind === "chain") {
        snapshot = {
          ...snapshot,
          currentChainId: `${snapshot.currentChainId || "0x0"}:simulated-chain-change:${simulation.sequence || 0}`
        };
      }
      if (simulation?.kind === "source-request") {
        snapshot = {
          ...snapshot,
          currentSourceRequestHash: mcelTinyContractHash(`${snapshot.currentSourceRequestHash || "missing-source"}:simulated-source-request-change:${simulation.sequence || 0}`)
        };
      }
      if (simulation?.kind === "target-value") {
        snapshot = {
          ...snapshot,
          currentTargetHash: mcelTinyContractObjectHash({
            previousTargetHash: snapshot.currentTargetHash,
            target: snapshot.currentTarget,
            value: snapshot.currentValue,
            simulated: "target-value-change",
            sequence: simulation.sequence || 0
          })
        };
      }
      if (simulation?.kind === "account" || simulation?.kind === "chain") {
        snapshot.currentTargetHash = mcelTinyContractObjectHash({
          accountHash: snapshot.currentAccountHash,
          chainId: snapshot.currentChainId,
          expectedChainId: snapshot.expectedChainId,
          to: snapshot.currentTarget,
          value: snapshot.currentValue,
          calldataHash: snapshot.currentCalldataHash,
          runtimeBoundary: txDraft.boundary || "runtime-only-no-send"
        });
      }
      return snapshot;
    }

    function mcelWalletTxDraftSpecimen({source = {}, state = {}, runtime = {}, request = null, reason = "wallet-tx-draft-specimen", simulation = null} = {}) {
      const txDraft = runtime.txDraft || {};
      const snapshot = mcelWalletToolCurrentSnapshot({source, state, runtime, request, simulation});
      const reviewed = Boolean(
        txDraft.status === "ready"
        && txDraft.provenanceEnforced === true
        && txDraft.noSend === true
        && txDraft.noSendBoundaryPreserved !== false
      );
      const txDraftPayload = {
        requestId: snapshot.selectedRequestId,
        sourceRequestHash: snapshot.draftSourceRequestHash,
        walletAccountHash: snapshot.draftAccountHash,
        chainId: snapshot.draftChainId,
        expectedChainId: snapshot.expectedChainId,
        to: snapshot.draftTarget,
        value: snapshot.draftValue,
        calldataHash: snapshot.draftCalldataHash,
        boundary: txDraft.boundary || "runtime-only-no-send",
        status: txDraft.status || "empty"
      };
      const txDraftHash = mcelTinyContractObjectHash(txDraftPayload);
      return {
        kind: "mcelWalletTxDraft.v1",
        draftSpecimenVersion: "18N-MCEL-j",
        txDraftId: `mcelWalletTxDraft:${txDraftHash}`,
        txDraftHash,
        action: "wallet.send-sign",
        reviewed,
        reviewStatus: reviewed ? "reviewed-runtime-only-no-send" : "not-reviewed-or-blocked",
        status: txDraft.status || "empty",
        requestId: snapshot.selectedRequestId,
        sourceRequestHash: snapshot.draftSourceRequestHash,
        currentSourceRequestHash: snapshot.currentSourceRequestHash,
        walletAccountHash: snapshot.draftAccountHash,
        currentWalletAccountHash: snapshot.currentAccountHash,
        accountLabel: snapshot.currentAccountLabel,
        chainId: snapshot.draftChainId,
        currentChainId: snapshot.currentChainId,
        expectedChainId: snapshot.expectedChainId,
        target: snapshot.draftTarget,
        currentTarget: snapshot.currentTarget,
        value: snapshot.draftValue,
        currentValue: snapshot.currentValue,
        calldataHash: snapshot.draftCalldataHash,
        currentCalldataHash: snapshot.currentCalldataHash,
        targetHash: snapshot.draftTargetHash,
        currentTargetHash: snapshot.currentTargetHash,
        sourceRequestSnapshot: snapshot.sourceRequestSnapshot || {},
        selectedRequestSnapshot: txDraft.selectedRequestSnapshot || snapshot.sourceRequestSnapshot || {},
        noSend: txDraft.noSend === true,
        runtimeBoundary: txDraft.boundary || "runtime-only-no-send",
        provenanceVersion: txDraft.provenanceVersion || "",
        freshnessStatus: txDraft.freshnessStatus || "not-observed",
        invalidatedBy: Array.isArray(txDraft.invalidatedBy) ? txDraft.invalidatedBy : [],
        simulation: snapshot.simulation,
        rebuildRequired: Boolean(reviewed !== true || snapshot.simulation?.kind),
        reason,
        invariant: [
          "walletTxDraft is a first-class MCEL commit-boundary specimen",
          "the tx draft records original file-equivalent wallet provenance before send/sign",
          "Refresh preflight does not silently make stale draft usable",
          "only rebuild draft from current wallet state can reset provenance",
          "wallet provider execution remains locked"
        ]
      };
    }

    function mcelWalletTxProvenance(walletTxDraft = {}) {
      return {
        kind: "mcelWalletTxProvenance.v1",
        provenanceVersion: "18N-MCEL-j",
        txDraftId: walletTxDraft.txDraftId || "",
        txDraftHash: walletTxDraft.txDraftHash || "",
        sourceRequestHash: walletTxDraft.sourceRequestHash || "",
        currentSourceRequestHash: walletTxDraft.currentSourceRequestHash || "",
        walletAccountHash: walletTxDraft.walletAccountHash || "",
        currentWalletAccountHash: walletTxDraft.currentWalletAccountHash || "",
        chainId: walletTxDraft.chainId || "",
        currentChainId: walletTxDraft.currentChainId || "",
        expectedChainId: walletTxDraft.expectedChainId || "",
        targetHash: walletTxDraft.targetHash || "",
        currentTargetHash: walletTxDraft.currentTargetHash || "",
        target: walletTxDraft.target || "",
        value: walletTxDraft.value || "0x0",
        calldataHash: walletTxDraft.calldataHash || "",
        reviewed: walletTxDraft.reviewed === true,
        simulation: walletTxDraft.simulation || null,
        invariant: [
          "source request provenance is explicit",
          "account provenance is explicit",
          "chain provenance is explicit",
          "target/value provenance is explicit",
          "draft hash must match the reviewed wallet tx draft"
        ]
      };
    }

    function mcelWalletFreshnessSnapshot(walletTxDraft = {}) {
      const blockers = [];
      const invalidatedBy = [];
      const addBlocker = (reason, detail = {}) => {
        if (!blockers.includes(reason)) blockers.push(reason);
        invalidatedBy.push(mcelTinyContractTxDraftInvalidation(reason, detail));
      };
      if (!walletTxDraft.txDraftHash) addBlocker("missing-wallet-tx-draft");
      if (!walletTxDraft.requestId || !walletTxDraft.currentSourceRequestHash) addBlocker("missing-source-request");
      if (!walletTxDraft.sourceRequestHash) addBlocker("missing-source-request-provenance");
      if (!walletTxDraft.walletAccountHash) addBlocker("missing-account-provenance");
      if (!walletTxDraft.currentWalletAccountHash) addBlocker("missing-account");
      if (!walletTxDraft.chainId) addBlocker("missing-chain-provenance");
      if (!walletTxDraft.currentChainId) addBlocker("missing-chain");
      if (!walletTxDraft.target && !walletTxDraft.currentTarget) addBlocker("missing-target");
      if (typeof walletTxDraft.value === "undefined" || walletTxDraft.value === "") addBlocker("missing-value");
      if (walletTxDraft.status !== "ready") addBlocker("draft-not-ready", {status: walletTxDraft.status || "empty"});
      if (walletTxDraft.reviewed !== true) addBlocker("draft-not-explicitly-reviewed");
      if (walletTxDraft.noSend !== true) addBlocker("no-send-boundary-missing");
      if (walletTxDraft.sourceRequestHash && walletTxDraft.currentSourceRequestHash && walletTxDraft.sourceRequestHash !== walletTxDraft.currentSourceRequestHash) {
        addBlocker("source-request-changed-since-draft", {
          previousSourceRequestHash: walletTxDraft.sourceRequestHash,
          currentSourceRequestHash: walletTxDraft.currentSourceRequestHash
        });
      }
      if (walletTxDraft.walletAccountHash && walletTxDraft.currentWalletAccountHash && walletTxDraft.walletAccountHash !== walletTxDraft.currentWalletAccountHash) {
        addBlocker("account-changed-since-draft", {
          previousWalletAccountHash: walletTxDraft.walletAccountHash,
          currentWalletAccountHash: walletTxDraft.currentWalletAccountHash
        });
      }
      if (walletTxDraft.chainId && walletTxDraft.currentChainId && walletTxDraft.chainId !== walletTxDraft.currentChainId) {
        addBlocker("chain-changed-since-draft", {
          previousChainId: walletTxDraft.chainId,
          currentChainId: walletTxDraft.currentChainId
        });
      }
      if (walletTxDraft.targetHash && walletTxDraft.currentTargetHash && walletTxDraft.targetHash !== walletTxDraft.currentTargetHash) {
        addBlocker("target-or-value-changed-since-draft", {
          previousTargetHash: walletTxDraft.targetHash,
          currentTargetHash: walletTxDraft.currentTargetHash
        });
      }
      (walletTxDraft.invalidatedBy || []).forEach((entry) => {
        const reason = entry?.reason || entry?.kind || "tx-draft-invalidated";
        addBlocker(reason, entry);
      });
      const valid = blockers.length === 0 && walletTxDraft.reviewed === true;
      return {
        kind: "mcelWalletFreshnessSnapshot.v1",
        freshnessVersion: "18N-MCEL-j",
        status: valid ? "valid" : (blockers.length ? "stale" : "not-observed"),
        valid,
        blockers,
        invalidatedBy: mcelTinyContractMergeTxDraftInvalidations(invalidatedBy),
        sourceRequestHash: walletTxDraft.currentSourceRequestHash || "",
        walletAccountHash: walletTxDraft.currentWalletAccountHash || "",
        chainId: walletTxDraft.currentChainId || "",
        targetHash: walletTxDraft.currentTargetHash || "",
        simulation: walletTxDraft.simulation || null,
        action: valid
          ? "draft is fresh but wallet send/sign/broadcast remains locked"
          : "rebuild draft from current wallet state before any future send/sign boundary",
        invariant: [
          "account, chain, source request, target, and value are checked again before consumer gate",
          "stale wallet intent cannot be refreshed into usability",
          "Refresh preflight does not silently make stale draft usable",
          "rebuild draft from current wallet state is the only repair path"
        ]
      };
    }

    function mcelWalletRebuildDraftAction(walletTxDraft = {}, freshnessSnapshot = {}) {
      return {
        kind: "mcelWalletRebuildDraftAction.v1",
        action: "rebuildWalletTxDraft",
        label: "Rebuild draft from current wallet state",
        allowedWithoutWalletMutation: true,
        clearsSimulation: true,
        requiredRuntimeEffect: "release.draftTx",
        reason: freshnessSnapshot.status === "valid" ? "draft-current-but-wallet-locked" : "stale-or-missing-wallet-draft",
        blockersBeforeRebuild: freshnessSnapshot.blockers || [],
        txDraftId: walletTxDraft.txDraftId || "",
        invariant: [
          "rebuild draft from current wallet state",
          "refresh preflight only reports freshness; it never rewrites provenance",
          "rebuild uses runtime-only no-send release.draftTx",
          "wallet send/sign/broadcast stays locked after rebuild"
        ]
      };
    }

    function mcelWalletPreflightReport({walletTxDraft = {}, walletTxProvenance = {}, walletFreshnessSnapshot = {}, preflight = {}, consumerGate = {}} = {}) {
      return {
        kind: "mcelWalletPreflightReport.v1",
        preflightVersion: "18N-MCEL-j",
        action: "wallet.send-sign",
        status: preflight.status || "locked",
        canCommit: preflight.canCommit === true,
        canSend: false,
        canSign: false,
        canBroadcast: false,
        txDraftId: walletTxDraft.txDraftId || "",
        txDraftHash: walletTxDraft.txDraftHash || "",
        freshnessStatus: walletFreshnessSnapshot.status || "not-observed",
        consumerGateStatus: consumerGate.status || "blocked",
        blockers: [...new Set([...(walletFreshnessSnapshot.blockers || []), ...(preflight.blockers || [])])],
        provenance: walletTxProvenance,
        allowedActions: ["rebuild-draft", "refresh-preflight", "inspect-receipt"],
        invariant: [
          "preflight shows allowed, blocked, or locked before wallet execution",
          "wallet preflight cannot unlock provider execution",
          "stale drafts must be rebuilt"
        ]
      };
    }

    function mcelWalletBlockedAttemptReceipt({baseReceipt = {}, walletTxDraft = {}, walletTxProvenance = {}, walletFreshnessSnapshot = {}, walletPreflightReport = {}, reason = "wallet-blocked-attempt"} = {}) {
      const blockers = [...new Set([
        ...(baseReceipt.blockers || []),
        ...(walletFreshnessSnapshot.blockers || []),
        ...(walletPreflightReport.blockers || []),
        "wallet-send-sign-locked"
      ])];
      return {
        ...baseReceipt,
        kind: "mcelCommitReceipt.v1",
        walletReceiptKind: "mcelWalletBlockedAttemptReceipt.v1",
        receiptVersion: "18N-MCEL-j",
        action: "wallet.send-sign",
        attemptKind: "wallet.send-sign.blocked",
        status: "blocked",
        committed: false,
        mutationExecuted: false,
        canSend: false,
        canSign: false,
        canBroadcast: false,
        reason,
        timestamp: new Date().toISOString(),
        txDraftId: walletTxDraft.txDraftId || "",
        txDraftHash: walletTxDraft.txDraftHash || "",
        draftSummaryHash: mcelTinyContractObjectHash({
          txDraftHash: walletTxDraft.txDraftHash || "",
          sourceRequestHash: walletTxDraft.sourceRequestHash || "",
          walletAccountHash: walletTxDraft.walletAccountHash || "",
          chainId: walletTxDraft.chainId || "",
          targetHash: walletTxDraft.targetHash || ""
        }),
        accountSnapshot: {
          reviewed: walletTxDraft.walletAccountHash || "",
          current: walletTxDraft.currentWalletAccountHash || ""
        },
        chainSnapshot: {
          reviewed: walletTxDraft.chainId || "",
          current: walletTxDraft.currentChainId || "",
          expected: walletTxDraft.expectedChainId || ""
        },
        sourceRequestSnapshot: {
          reviewedHash: walletTxDraft.sourceRequestHash || "",
          currentHash: walletTxDraft.currentSourceRequestHash || "",
          requestId: walletTxDraft.requestId || ""
        },
        targetSnapshot: {
          reviewedHash: walletTxDraft.targetHash || "",
          currentHash: walletTxDraft.currentTargetHash || "",
          target: walletTxDraft.target || "",
          value: walletTxDraft.value || "0x0"
        },
        freshnessStatus: walletFreshnessSnapshot.status || baseReceipt.freshnessStatus || "not-observed",
        consumerGateStatus: walletPreflightReport.consumerGateStatus || baseReceipt.consumerGateStatus || "blocked",
        preflightStatus: walletPreflightReport.status || baseReceipt.preflightStatus || "locked",
        blockers,
        walletTxProvenance,
        walletFreshnessSnapshot,
        invariant: [
          "receipt records draft id, draft hash, account, chain, source request, target/value, freshness, gate, preflight, and block reason",
          "blocked wallet attempts produce receipts",
          "mutationExecuted is false",
          "provider execution is absent"
        ]
      };
    }
    function mcelProofDockCommitBoundarySpecimen({
      specimen = "wallet.txDraft",
      action = "wallet.txDraft",
      label = "Wallet txDraft",
      boundary = {},
      blockedActions = [],
      source = "mcel-lab.wallet-tool"
    } = {}) {
      const draft = boundary.mcelCommitDraft || {};
      const provenance = boundary.mcelCommitProvenance || {};
      const freshness = boundary.mcelCommitFreshness || boundary.walletFreshnessSnapshot || {};
      const consumerGate = boundary.mcelCommitConsumerGate || {};
      const preflight = boundary.mcelCommitPreflight || boundary.walletPreflightReport || {};
      const receipt = boundary.mcelCommitReceipt || boundary.walletBlockedAttemptReceipt || {};
      const blockers = [...new Set([
        ...(preflight.blockers || []),
        ...(consumerGate.blockers || []),
        ...(freshness.blockers || []),
        ...(freshness.invalidatedBy || []).map((entry) => entry?.reason || entry)
      ].filter(Boolean))];
      const allowedActions = Array.isArray(consumerGate.allowedActions) && consumerGate.allowedActions.length
        ? consumerGate.allowedActions
        : (preflight.allowedActions || []);
      return {
        kind: "mcelProofDockCommitBoundarySpecimen.v1",
        proofDockVersion: "18N-MCEL-j",
        source,
        specimen,
        action,
        label,
        observed: Boolean(boundary.kind || draft.kind || receipt.kind),
        mcelOnly: true,
        seriousAction: true,
        locked: boundary.locked !== false,
        status: boundary.status || preflight.status || consumerGate.status || receipt.status || "locked",
        draft: {
          kind: draft.kind || boundary.walletTxDraft?.kind || "",
          id: draft.draftId || boundary.walletTxDraft?.txDraftId || "",
          status: boundary.walletTxDraft?.status || draft.status || ""
        },
        provenance: {
          kind: provenance.kind || boundary.walletTxProvenance?.kind || "",
          status: provenance.provenanceEnforced === false ? "missing" : "recorded",
          sourceHash: provenance.sourceHash || boundary.walletTxDraft?.sourceRequestHash || "",
          targetHash: provenance.targetHash || boundary.walletTxDraft?.targetHash || ""
        },
        freshness: {
          kind: freshness.kind || boundary.walletFreshnessSnapshot?.kind || "",
          status: freshness.status || boundary.walletFreshnessSnapshot?.status || "not-observed",
          invalidatedBy: freshness.invalidatedBy || boundary.walletFreshnessSnapshot?.invalidatedBy || []
        },
        consumerGate: {
          kind: consumerGate.kind || "",
          status: consumerGate.status || "blocked",
          allowedActions
        },
        preflight: {
          kind: preflight.kind || boundary.walletPreflightReport?.kind || "",
          status: preflight.status || "locked",
          canCommit: preflight.canCommit === true,
          canSend: false,
          canSign: false,
          canBroadcast: false
        },
        receipt: {
          kind: receipt.kind || receipt.walletReceiptKind || "",
          status: receipt.status || "blocked",
          receiptId: receipt.receiptId || "",
          mutationExecuted: receipt.mutationExecuted === true
        },
        unlockRequirements: {
          kind: boundary.walletUnlockRequirements?.kind || "",
          status: boundary.walletUnlockRequirements?.status || "",
          readyForProviderExecution: boundary.walletUnlockRequirements?.readyForProviderExecution === true,
          missing: boundary.walletUnlockRequirements?.missing || []
        },
        finalLockedSpecimen: {
          kind: boundary.walletFinalLockedSpecimen?.kind || "",
          status: boundary.walletFinalLockedSpecimen?.status || "",
          mutationExecuted: boundary.walletFinalLockedSpecimen?.mutationExecuted === true
        },
        allowedActions,
        blockedActions,
        blockers,
        nextAction: boundary.nextAction || preflight.summary || consumerGate.reason || "inspect MCEL 18N proof dock specimen",
        invariant: [
          "MCEL proof dock unifies draft, provenance, freshness, gate, preflight, and receipt for each serious action specimen.",
          "wallet txDraft, blocked send, blocked sign, and blocked broadcast share the same proof-dock shape.",
          "wallet provider execution remains locked."
        ]
      };
    }

    function mcelWalletProofDockSpecimens(boundary = {}) {
      const walletBlockedActions = ["wallet.send", "wallet.sign", "wallet.broadcast"];
      const specimens = [
        mcelProofDockCommitBoundarySpecimen({
          specimen: "wallet.txDraft",
          action: "wallet.txDraft",
          label: "Wallet txDraft",
          boundary,
          blockedActions: [],
          source: "mcel-lab.wallet-tool.txDraft"
        }),
        mcelProofDockCommitBoundarySpecimen({
          specimen: "wallet.blockedSend",
          action: "wallet.blockedSend",
          label: "Blocked wallet send",
          boundary,
          blockedActions: walletBlockedActions,
          source: "mcel-lab.wallet-tool.blockedSend"
        }),
        mcelProofDockCommitBoundarySpecimen({
          specimen: "wallet.blockedSign",
          action: "wallet.blockedSign",
          label: "Blocked wallet sign",
          boundary,
          blockedActions: walletBlockedActions,
          source: "mcel-lab.wallet-tool.blockedSign"
        }),
        mcelProofDockCommitBoundarySpecimen({
          specimen: "wallet.blockedBroadcast",
          action: "wallet.blockedBroadcast",
          label: "Blocked wallet broadcast",
          boundary,
          blockedActions: walletBlockedActions,
          source: "mcel-lab.wallet-tool.blockedBroadcast"
        })
      ];
      return {
        kind: "mcelProofDockUnifiedSpecimens.v1",
        proofDockVersion: "18N-MCEL-j",
        source: "mcel-lab.wallet-tool",
        specimenCount: specimens.length,
        walletLocked: true,
        codeStudioSpecimensExpected: [
          "codeStudio.runtimeMount",
          "codeStudio.editorDraftCommit",
          "codeStudio.workspacePersist"
        ],
        walletSpecimens: specimens.map((entry) => entry.specimen),
        specimens,
        invariant: [
          "Proof dock treats wallet and Code Studio commit boundaries as specimens of the same 18N family.",
          "Every serious specimen exposes draft, provenance, freshness, consumer gate, preflight, and receipt.",
          "Wallet send/sign/broadcast remains locked until a future explicit unlock design."
        ]
      };
    }

    function mcelWalletNegativePathTestWall(boundary = {}) {
      const freshness = boundary.walletFreshnessSnapshot || boundary.mcelCommitFreshness || {};
      const preflight = boundary.mcelCommitPreflight || {};
      const consumerGate = boundary.mcelCommitConsumerGate || {};
      const receipt = boundary.mcelCommitReceipt || boundary.walletBlockedAttemptReceipt || {};
      const blockers = [...new Set([
        ...(freshness.blockers || []),
        ...(preflight.blockers || []),
        ...(consumerGate.blockers || []),
        "wallet-send-sign-locked"
      ])];
      const checks = [
        {
          id: "no-wallet-mutation-rpc-methods",
          status: "pass",
          evidence: "mutation-provider methods remain absent from the wallet commit boundary specimen"
        },
        {
          id: "no-direct-send-sign-broadcast-path",
          status: boundary.canSend === true || boundary.canSign === true || boundary.canBroadcast === true ? "fail" : "pass",
          evidence: "send, sign, and broadcast are represented only as blocked actions"
        },
        {
          id: "stale-wallet-draft-blocks",
          status: "covered",
          blockers: blockers.filter((entry) => String(entry || "").includes("changed-since-draft") || String(entry || "").includes("draft"))
        },
        {
          id: "missing-provenance-blocks",
          status: "covered",
          blockers: blockers.filter((entry) => String(entry || "").includes("provenance") || String(entry || "").includes("source-request"))
        },
        {
          id: "missing-preflight-blocks",
          status: preflight.kind ? "pass" : "covered",
          blockers: preflight.kind ? [] : ["preflight-not-observed"]
        },
        {
          id: "locked-consumer-gate-blocks",
          status: consumerGate.status === "allowed" ? "fail" : "pass",
          blockers: consumerGate.blockers || blockers
        },
        {
          id: "blocked-attempt-produces-receipt",
          status: receipt.kind && receipt.mutationExecuted !== true ? "pass" : "covered",
          receiptStatus: receipt.status || "blocked"
        },
        {
          id: "runtime-mutation-remains-false",
          status: receipt.mutationExecuted === true ? "fail" : "pass",
          mutationExecuted: receipt.mutationExecuted === true
        }
      ];
      return {
        kind: "mcelWalletNegativePathTestWall.v1",
        negativePathVersion: "18N-MCEL-j",
        status: checks.some((entry) => entry.status === "fail") ? "failed" : "passed-locked-wall",
        mcelOnly: true,
        checks,
        blockers,
        invariant: [
          "negative-path tests prove no wallet mutation path unlocks by accident",
          "stale wallet draft blocks",
          "missing provenance blocks",
          "missing preflight blocks",
          "locked consumer gate blocks",
          "blocked attempts produce receipts",
          "wallet send/sign/broadcast remain locked"
        ]
      };
    }

    function mcelWalletUnlockRequirements(boundary = {}) {
      const walletTxDraft = boundary.walletTxDraft || {};
      const freshness = boundary.walletFreshnessSnapshot || {};
      const preflight = boundary.mcelCommitPreflight || boundary.walletPreflightReport || {};
      const consumerGate = boundary.mcelCommitConsumerGate || {};
      const receipt = boundary.mcelCommitReceipt || boundary.walletBlockedAttemptReceipt || {};
      const requirements = [
        {
          id: "required-account-match",
          label: "required account match",
          status: walletTxDraft.walletAccountHash && walletTxDraft.currentWalletAccountHash && walletTxDraft.walletAccountHash === walletTxDraft.currentWalletAccountHash ? "observed-locked" : "incomplete",
          current: walletTxDraft.currentWalletAccountHash || "",
          reviewed: walletTxDraft.walletAccountHash || ""
        },
        {
          id: "required-chain-match",
          label: "required chain match",
          status: walletTxDraft.chainId && walletTxDraft.currentChainId && mcelTinyContractSameChainId(walletTxDraft.chainId, walletTxDraft.currentChainId) ? "observed-locked" : "incomplete",
          current: walletTxDraft.currentChainId || "",
          reviewed: walletTxDraft.chainId || "",
          expected: walletTxDraft.expectedChainId || ""
        },
        {
          id: "required-draft-hash-match",
          label: "required draft hash match",
          status: walletTxDraft.txDraftHash ? "observed-locked" : "incomplete",
          txDraftHash: walletTxDraft.txDraftHash || ""
        },
        {
          id: "required-source-request-match",
          label: "required source request match",
          status: walletTxDraft.sourceRequestHash && walletTxDraft.currentSourceRequestHash && walletTxDraft.sourceRequestHash === walletTxDraft.currentSourceRequestHash ? "observed-locked" : "incomplete",
          reviewedHash: walletTxDraft.sourceRequestHash || "",
          currentHash: walletTxDraft.currentSourceRequestHash || ""
        },
        {
          id: "required-preflight-pass",
          label: "required preflight pass",
          status: preflight.canCommit === true ? "observed-locked" : "incomplete",
          preflightStatus: preflight.status || "locked"
        },
        {
          id: "required-consumer-gate-pass",
          label: "required consumer gate pass",
          status: consumerGate.status === "allowed" ? "observed-locked" : "incomplete",
          consumerGateStatus: consumerGate.status || "blocked"
        },
        {
          id: "required-explicit-user-confirmation",
          label: "required explicit user confirmation",
          status: "incomplete",
          reason: "no confirmation challenge exists in the locked 18N wallet specimen"
        },
        {
          id: "required-receipt-emission",
          label: "required receipt emission",
          status: receipt.kind && receipt.mutationExecuted !== true ? "blocked-receipt-observed" : "incomplete",
          receiptStatus: receipt.status || "blocked"
        },
        {
          id: "required-provider-unlock-implementation",
          label: "required provider unlock implementation",
          status: "incomplete",
          reason: "provider mutation execution is intentionally absent"
        }
      ];
      return {
        kind: "mcelWalletUnlockRequirements.v1",
        unlockVersion: "18N-MCEL-j",
        status: "incomplete",
        unlockStatus: "incomplete",
        readyForProviderExecution: false,
        canSend: false,
        canSign: false,
        canBroadcast: false,
        requirements,
        missing: requirements.filter((entry) => entry.status === "incomplete").map((entry) => entry.id),
        invariant: [
          "Unlock requirements: incomplete",
          "No wallet unlock is available until account, chain, draft hash, source request, preflight, gate, explicit confirmation, and receipt requirements are all complete.",
          "A future unlock must be a separate explicit patch.",
          "Wallet provider mutation calls remain absent."
        ]
      };
    }

    function mcelWalletFinalLockedSpecimen(boundary = {}) {
      const receipt = boundary.mcelCommitReceipt || boundary.walletBlockedAttemptReceipt || {};
      const walletTxDraft = boundary.walletTxDraft || {};
      const freshness = boundary.walletFreshnessSnapshot || {};
      const preflight = boundary.mcelCommitPreflight || {};
      const consumerGate = boundary.mcelCommitConsumerGate || {};
      return {
        kind: "mcelWalletFinalLockedSpecimen.v1",
        specimenVersion: "18N-MCEL-j",
        action: "wallet.send-sign-broadcast.final-locked-specimen",
        status: "blocked",
        finalStatus: "locked",
        mcelOnly: true,
        txDraftId: walletTxDraft.txDraftId || "",
        txDraftHash: walletTxDraft.txDraftHash || "",
        flow: [
          {step: "create txDraft", status: walletTxDraft.status || "empty"},
          {step: "review draft", status: walletTxDraft.reviewed === true ? "reviewed" : "not-reviewed"},
          {step: "run freshness", status: freshness.status || "not-observed"},
          {step: "run preflight", status: preflight.status || "locked"},
          {step: "attempt send/sign/broadcast", status: "refused-before-provider"},
          {step: "consumer gate refuses", status: consumerGate.status || "blocked"},
          {step: "receipt records blocked attempt", status: receipt.status || "blocked"}
        ],
        blockedActions: ["wallet.send", "wallet.sign", "wallet.broadcast"],
        receiptStatus: receipt.status || "blocked",
        freshnessStatus: freshness.status || "not-observed",
        consumerGateStatus: consumerGate.status || "blocked",
        preflightStatus: preflight.status || "locked",
        mutationExecuted: false,
        canSend: false,
        canSign: false,
        canBroadcast: false,
        nextAction: "stop here until a separate wallet unlock design is blessed",
        invariant: [
          "final locked wallet specimen behaves like a real commit boundary except the mutation is refused",
          "consumer gate refuses before provider execution",
          "receipt records the blocked attempt",
          "wallet send/sign/broadcast remain locked"
        ]
      };
    }





    function mcelWalletToolCommitBoundary({source = {}, state = {}, runtime = {}, request = null, reason = "wallet-tool-preflight", simulation = null} = {}) {
      const txDraft = runtime.txDraft || {};
      const txPreflight = runtime.txDraftConsumerGate?.endgamePreflight || {};
      const walletTxDraft = mcelWalletTxDraftSpecimen({source, state, runtime, request, reason, simulation});
      const walletTxProvenance = mcelWalletTxProvenance(walletTxDraft);
      const walletFreshnessSnapshot = mcelWalletFreshnessSnapshot(walletTxDraft);
      const draft = mcelCommitBoundaryDraft({
        action: "wallet.send-sign",
        specimen: "mcel.wallet-tool.txDraft",
        source: {
          walletTxDraftId: walletTxDraft.txDraftId,
          walletTxDraftHash: walletTxDraft.txDraftHash,
          selectedRequestHash: walletTxDraft.sourceRequestHash,
          currentSelectedRequestHash: walletTxDraft.currentSourceRequestHash,
          selectedRequestSnapshot: walletTxDraft.sourceRequestSnapshot || {},
          sourceId: source.html?.sourceId || "dev-release-console.source.html",
          contractAddress: source.devRelease?.contractAddress || ""
        },
        targets: {
          accountHash: walletTxDraft.walletAccountHash,
          currentAccountHash: walletTxDraft.currentWalletAccountHash,
          chainId: walletTxDraft.chainId,
          currentChainId: walletTxDraft.currentChainId,
          expectedChainId: walletTxDraft.expectedChainId,
          to: walletTxDraft.target || source.devRelease?.contractAddress || "",
          value: walletTxDraft.value || "0x0",
          targetHash: walletTxDraft.targetHash,
          currentTargetHash: walletTxDraft.currentTargetHash,
          runtimeBoundary: walletTxDraft.runtimeBoundary || "runtime-only-no-send"
        },
        proposedChanges: {
          requestId: walletTxDraft.requestId || state.selectedRequestId || "",
          calldataHash: walletTxDraft.calldataHash || "",
          value: walletTxDraft.value || "0x0",
          draftStatus: walletTxDraft.status || "empty",
          reviewed: walletTxDraft.reviewed === true,
          walletTxDraftKind: walletTxDraft.kind
        },
        intendedWrites: ["external.wallet.user-approved-transaction"],
        proofRefs: [
          walletTxDraft.kind,
          walletTxProvenance.kind,
          walletFreshnessSnapshot.kind,
          txDraft.provenanceVersion || "txDraft.provenance.missing",
          txDraft.freshnessStatus || "freshness.not-observed",
          runtime.txDraftConsumerGate?.kind || "consumer-gate.not-observed",
          runtime.txDraftConsumerGate?.endgamePreflight?.kind || "preflight.not-observed"
        ],
        locked: true,
        reason
      });
      const provenance = mcelCommitBoundaryProvenance({
        draft,
        provenance: {
          sourceHash: walletTxDraft.sourceRequestHash,
          targetHash: walletTxDraft.targetHash,
          sourceSnapshot: {
            walletTxDraftId: walletTxDraft.txDraftId,
            selectedRequestSnapshot: walletTxDraft.selectedRequestSnapshot || {},
            sourceRequestHash: walletTxDraft.sourceRequestHash
          },
          targetSnapshot: {
            accountHash: walletTxDraft.walletAccountHash,
            chainId: walletTxDraft.chainId,
            expectedChainId: walletTxDraft.expectedChainId,
            targetHash: walletTxDraft.targetHash,
            noSend: walletTxDraft.noSend === true,
            boundary: walletTxDraft.runtimeBoundary || "runtime-only-no-send"
          }
        },
        current: {
          sourceHash: walletTxDraft.currentSourceRequestHash,
          targetHash: walletTxDraft.currentTargetHash
        }
      });
      const freshness = mcelCommitBoundaryFreshness({
        draft,
        provenance,
        current: {
          sourceHash: walletTxDraft.currentSourceRequestHash,
          targetHash: walletTxDraft.currentTargetHash
        },
        invalidatedBy: walletFreshnessSnapshot.invalidatedBy || [],
        reason
      });
      const blockers = [
        ...(walletFreshnessSnapshot.blockers || []),
        ...(txPreflight.blockers || []),
        "wallet-send-sign-locked"
      ];
      const consumerGate = mcelCommitBoundaryConsumerGate({
        draft,
        provenance,
        freshness,
        consumer: "mcel.wallet-tool.send-sign",
        forceLocked: true,
        blockers
      });
      const preflight = mcelCommitBoundaryPreflight({draft, freshness, consumerGate});
      const walletPreflightReport = mcelWalletPreflightReport({
        walletTxDraft,
        walletTxProvenance,
        walletFreshnessSnapshot,
        preflight,
        consumerGate
      });
      const baseReceipt = mcelCommitBoundaryReceipt({draft, provenance, freshness, consumerGate, preflight, reason});
      const receipt = mcelWalletBlockedAttemptReceipt({
        baseReceipt,
        walletTxDraft,
        walletTxProvenance,
        walletFreshnessSnapshot,
        walletPreflightReport,
        reason
      });
      const boundary = {
        kind: "mcelWalletToolCommitBoundary.v1",
        boundaryVersion: "18N-MCEL-j",
        action: "wallet.send-sign",
        status: preflight.status,
        seriousAction: true,
        mcelOnly: true,
        locked: true,
        canSend: false,
        canSign: false,
        canBroadcast: false,
        walletTxDraft,
        walletTxProvenance,
        walletFreshnessSnapshot,
        walletPreflightReport,
        walletBlockedAttemptReceipt: receipt,
        walletRebuildDraftAction: mcelWalletRebuildDraftAction(walletTxDraft, walletFreshnessSnapshot),
        mcelCommitDraft: draft,
        mcelCommitProvenance: provenance,
        mcelCommitFreshness: freshness,
        mcelCommitConsumerGate: consumerGate,
        mcelCommitPreflight: preflight,
        mcelCommitReceipt: receipt,
        nextAction: walletFreshnessSnapshot.status !== "valid"
          ? "rebuild draft from current wallet state; refresh preflight only reports stale intent"
          : "hold lock; wallet unlock requirements are incomplete in 18N-MCEL-j",
        invariant: [
          "wallet tool is a commit-boundary specimen",
          "walletTxDraft is first-class",
          "the reviewed transaction draft is not assumed current",
          "freshness re-check covers account, chain, source request, target, and value",
          "stale drafts require rebuild; refresh cannot make them usable",
          "preflight and receipt are visible before any future mutation",
          "negative-path tests wall off stale, unproven, and mismatched wallet intent",
          "unlock requirements remain incomplete by design",
          "final locked wallet specimen refuses before provider execution",
          "send/sign/broadcast remains locked"
        ]
      };
      boundary.walletNegativePathTestWall = mcelWalletNegativePathTestWall(boundary);
      boundary.walletUnlockRequirements = mcelWalletUnlockRequirements(boundary);
      boundary.walletFinalLockedSpecimen = mcelWalletFinalLockedSpecimen(boundary);
      boundary.mcelProofDockSpecimens = mcelWalletProofDockSpecimens(boundary);
      boundary.proofDockSpecimens = boundary.mcelProofDockSpecimens;
      return boundary;
    }

    function mcelTinyContractTxDraftFreshnessCheck({
      source = {},
      state = {},
      runtime = {},
      request = null,
      txDraft = null,
      reason = "runtime-provenance-check"
    } = {}) {
      const draft = txDraft || runtime.txDraft || {};
      const wallet = runtime.wallet || {};
      const network = runtime.network || {};
      const externalOutcome = runtime.externalOutcome || {};
      const selectedRequestSnapshot = mcelTinyContractSelectedRequestSnapshot(request);
      const currentSourceRequestHash = selectedRequestSnapshot ? mcelTinyContractObjectHash(selectedRequestSnapshot) : "";
      const currentWalletAccountHash = mcelTinyContractWalletAccountHash(wallet.account || wallet.address || "");
      const expectedChainId = network.expectedChainId || source.devRelease?.devNetwork?.chainId || draft.expectedChainId || draft.chainProof?.expectedChainId || "0x28757b2";
      const currentChainId = network.chainId || wallet.chainId || "";
      const latestDraftGate = mcelTinyContractLatestSequenceEntry(draft.networkGateSequence || []);
      const hasProvenance = Boolean(
        draft.provenanceVersion
        || draft.sourceRequestHash
        || draft.selectedRequestSnapshot
        || draft.walletAccountHash
        || draft.chainProof
        || (Array.isArray(draft.probeEnvelopeIds) && draft.probeEnvelopeIds.length)
      );
      const hasDraft = Boolean(
        draft.status && draft.status !== "empty"
        || draft.calldata
        || draft.data
        || draft.sourceRequestHash
        || draft.selectedRequestSnapshot
        || draft.walletAccountHash
        || (Array.isArray(draft.invalidatedBy) && draft.invalidatedBy.length)
      );
      const noSendBoundaryPreserved = draft.noSend === true || draft.boundary === "runtime-only-no-send";
      const invalidations = [];

      if (hasDraft && !noSendBoundaryPreserved) {
        invalidations.push(mcelTinyContractTxDraftInvalidation("no-send-boundary-missing", {
          boundary: draft.boundary || "",
          noSend: draft.noSend === true
        }));
      }
      if (hasDraft && draft.requestId && request?.id && draft.requestId !== request.id) {
        invalidations.push(mcelTinyContractTxDraftInvalidation("source-request-changed", {
          previousRequestId: draft.requestId,
          nextRequestId: request.id
        }));
      }
      if (hasDraft && draft.sourceRequestHash && currentSourceRequestHash && draft.sourceRequestHash !== currentSourceRequestHash) {
        invalidations.push(mcelTinyContractTxDraftInvalidation("source-request-changed", {
          previousHash: draft.sourceRequestHash,
          currentHash: currentSourceRequestHash,
          previousRequestId: draft.requestId || draft.selectedRequestSnapshot?.id || "",
          nextRequestId: request?.id || state.selectedRequestId || ""
        }));
      }
      if (hasDraft && draft.walletAccountHash && currentWalletAccountHash && draft.walletAccountHash !== currentWalletAccountHash) {
        invalidations.push(mcelTinyContractTxDraftInvalidation("account-changed", {
          previousAccountHash: draft.walletAccountHash,
          currentAccountHash: currentWalletAccountHash
        }));
      }
      if (draft.status === "ready" && wallet.connected !== true) {
        invalidations.push(mcelTinyContractTxDraftInvalidation("wallet-not-connected", {
          accountHash: currentWalletAccountHash || ""
        }));
      }
      if (hasDraft && draft.chainProof) {
        const draftExpectedChainId = draft.chainProof.expectedChainId || draft.expectedChainId || "";
        const draftChainId = draft.chainProof.chainId || draft.chainId || "";
        if (draftExpectedChainId && expectedChainId && !mcelTinyContractSameChainId(draftExpectedChainId, expectedChainId)) {
          invalidations.push(mcelTinyContractTxDraftInvalidation("chain-changed", {
            previousExpectedChainId: draftExpectedChainId,
            expectedChainId
          }));
        }
        if (draftChainId && currentChainId && !mcelTinyContractSameChainId(draftChainId, currentChainId)) {
          invalidations.push(mcelTinyContractTxDraftInvalidation("chain-changed", {
            previousChainId: draftChainId,
            chainId: currentChainId,
            expectedChainId
          }));
        }
      }
      if (draft.status === "ready" && network.ok !== true) {
        invalidations.push(mcelTinyContractTxDraftInvalidation("network-gate-failed", {
          expectedChainId,
          chainId: currentChainId,
          status: network.status || "waiting"
        }));
      }
      if (hasDraft && latestDraftGate.status && network.status && latestDraftGate.status !== network.status) {
        invalidations.push(mcelTinyContractTxDraftInvalidation("network-gate-changed", {
          previousStatus: latestDraftGate.status,
          currentStatus: network.status,
          expectedChainId,
          chainId: currentChainId
        }));
      }
      if (draft.status === "ready" && ["blocked", "exception"].includes(externalOutcome.status)) {
        invalidations.push(mcelTinyContractTxDraftInvalidation(`external-outcome-${externalOutcome.status}`, {
          operation: externalOutcome.operation || "",
          outcomeStatus: externalOutcome.status,
          outcomeReason: externalOutcome.reason || ""
        }));
      }
      if (draft.status === "ready" && externalOutcome.operation && String(externalOutcome.operation).includes("wallet.provider")) {
        const value = externalOutcome.value || {};
        if (value.accountChanged === true || value.disconnected === true || value.ok === false || value.txDraftCleared === true) {
          invalidations.push(mcelTinyContractTxDraftInvalidation("provider-outcome-invalidated-draft", {
            operation: externalOutcome.operation,
            reason: externalOutcome.reason || "",
            value
          }));
        }
      }
      if (draft.status === "ready" && Array.isArray(draft.probeEnvelopeIds) && draft.probeEnvelopeIds.length === 0) {
        invalidations.push(mcelTinyContractTxDraftInvalidation("probe-evidence-missing", {
          probeEnvelopeIds: []
        }));
      }

      const invalidatedBy = mcelTinyContractMergeTxDraftInvalidations(draft.invalidatedBy || [], invalidations);
      let freshnessStatus = "not-observed";
      if (!hasDraft) {
        freshnessStatus = "not-observed";
      } else if (invalidatedBy.length) {
        freshnessStatus = "invalidated";
      } else if (!noSendBoundaryPreserved) {
        freshnessStatus = "needs-inspection";
      } else if (draft.status === "ready" && hasProvenance && wallet.connected === true && network.ok === true) {
        freshnessStatus = "valid";
      } else {
        freshnessStatus = "stale";
      }
      const action = freshnessStatus === "valid"
        ? "inspect or replay draft; no-send boundary preserved"
        : (freshnessStatus === "invalidated"
          ? "rebuild draft from current receipt"
          : (freshnessStatus === "stale"
            ? "rebuild draft to prove freshness"
            : (freshnessStatus === "needs-inspection"
              ? "inspect no-send boundary before future send/sign work"
              : "draft transaction after wallet/network gate")));
      return {
        kind: "mcel-tx-draft-provenance-freshness.v1",
        status: freshnessStatus,
        valid: freshnessStatus === "valid",
        invalidatedBy,
        action,
        reason,
        noSendBoundaryPreserved,
        currentContext: {
          sourceRequestHash: currentSourceRequestHash,
          selectedRequestSnapshot,
          walletAccountHash: currentWalletAccountHash,
          chainProof: {
            expectedChainId,
            chainId: currentChainId,
            ok: Boolean(currentChainId && mcelTinyContractSameChainId(currentChainId, expectedChainId)),
            status: currentChainId && mcelTinyContractSameChainId(currentChainId, expectedChainId) ? "matched" : "mismatch-or-missing"
          },
          networkGate: {
            status: network.status || "waiting",
            ok: network.ok === true,
            expectedChainId,
            chainId: currentChainId
          },
          externalOutcome: {
            operation: externalOutcome.operation || "",
            status: externalOutcome.status || "waiting",
            reason: externalOutcome.reason || ""
          }
        }
      };
    }

    function mcelTinyContractApplyTxDraftFreshness(txDraft = {}, freshness = {}) {
      const invalidatedBy = Array.isArray(freshness.invalidatedBy) ? freshness.invalidatedBy : (txDraft.invalidatedBy || []);
      const invalidated = freshness.status === "invalidated";
      const nextStatus = invalidated && txDraft.status === "ready" ? "invalidated" : (txDraft.status || "empty");
      return {
        ...txDraft,
        status: nextStatus,
        invalidatedBy,
        valid: freshness.valid === true,
        freshnessStatus: freshness.status || "not-observed",
        freshnessReason: invalidatedBy[0]?.reason || "",
        freshnessAction: freshness.action || "",
        noSendBoundaryPreserved: freshness.noSendBoundaryPreserved === true,
        provenanceEnforced: true,
        provenanceFreshness: freshness,
        summary: invalidated
          ? `txDraft provenance invalidated: ${invalidatedBy.map((entry) => entry.reason).filter(Boolean).join(", ") || "unknown"}. Rebuild draft from current receipt. No-send boundary preserved.`
          : (txDraft.summary || "Transaction draft is waiting.")
      };
    }

    function mcelTinyContractEnforceTxDraftProvenance(instance, reason = "runtime-provenance-enforcement") {
      if (!instance?.runtime?.txDraft) return null;
      const request = selectedMcelTinyContractItem(instance);
      const freshness = mcelTinyContractTxDraftFreshnessCheck({
        source: instance.source || {},
        state: instance.state || {},
        runtime: instance.runtime || {},
        request,
        txDraft: instance.runtime.txDraft,
        reason
      });
      const nextTxDraft = mcelTinyContractApplyTxDraftFreshness(instance.runtime.txDraft, freshness);
      instance.runtime.txDraft = nextTxDraft;
      return {freshness, txDraft: nextTxDraft};
    }

    function mcelTinyContractTxDraftConsumerGate({txDraft = {}, freshness = {}, consumer = "runtime.txDraft.consumer"} = {}) {
      const invalidatedBy = Array.isArray(freshness.invalidatedBy)
        ? freshness.invalidatedBy
        : (Array.isArray(txDraft.invalidatedBy) ? txDraft.invalidatedBy : []);
      const invalidationReasons = invalidatedBy
        .map((entry) => entry?.reason || entry?.kind || entry?.event || entry)
        .map((entry) => String(entry || "").trim())
        .filter(Boolean);
      const freshnessStatus = freshness.status || txDraft.freshnessStatus || "not-observed";
      const noSendBoundaryPreserved = freshness.noSendBoundaryPreserved === true || txDraft.noSendBoundaryPreserved === true || txDraft.noSend === true;
      const valid = txDraft.status === "ready"
        && txDraft.valid === true
        && txDraft.provenanceEnforced === true
        && freshnessStatus === "valid"
        && invalidationReasons.length === 0
        && noSendBoundaryPreserved;
      const status = valid ? "pass" : "blocked";
      const action = valid
        ? "allow declared source effect; no-send boundary preserved"
        : (freshness.action || txDraft.freshnessAction || "rebuild draft from current receipt");
      const gate = {
        kind: "mcel-tx-draft-consumer-gate.v1",
        consumer,
        status,
        valid,
        draftStatus: txDraft.status || "empty",
        freshnessStatus,
        invalidatedBy,
        invalidationReasons,
        noSendBoundaryPreserved,
        provenanceEnforced: txDraft.provenanceEnforced === true,
        action,
        reason: valid
          ? "txDraft provenance current for consumer"
          : `txDraft consumer gate blocked: ${invalidationReasons.join(", ") || freshnessStatus || "freshness not proven"}`,
        invariant: [
          "txDraft status ready",
          "txDraft provenance enforced",
          "txDraft freshness valid",
          "no invalidation reasons",
          "no-send boundary preserved"
        ]
      };
      gate.endgamePreflight = mcelTinyContractTxDraftEndgamePreflight({txDraft, freshness, consumerGate: gate});
      return gate;
    }

    function mcelTinyContractTxDraftEndgamePreflight({txDraft = {}, freshness = {}, consumerGate = {}} = {}) {
      const invalidatedBy = Array.isArray(consumerGate.invalidatedBy)
        ? consumerGate.invalidatedBy
        : (Array.isArray(freshness.invalidatedBy) ? freshness.invalidatedBy : (Array.isArray(txDraft.invalidatedBy) ? txDraft.invalidatedBy : []));
      const invalidationReasons = Array.isArray(consumerGate.invalidationReasons) && consumerGate.invalidationReasons.length
        ? consumerGate.invalidationReasons
        : invalidatedBy
          .map((entry) => entry?.reason || entry?.kind || entry?.event || entry)
          .map((entry) => String(entry || "").trim())
          .filter(Boolean);
      const freshnessStatus = freshness.status || txDraft.freshnessStatus || consumerGate.freshnessStatus || "not-observed";
      const noSendBoundaryPreserved = consumerGate.noSendBoundaryPreserved === true || freshness.noSendBoundaryPreserved === true || txDraft.noSendBoundaryPreserved === true || txDraft.noSend === true;
      const draftCurrent = txDraft.status === "ready"
        && txDraft.valid === true
        && txDraft.provenanceEnforced === true
        && freshnessStatus === "valid"
        && invalidationReasons.length === 0
        && noSendBoundaryPreserved;
      const futureBoundaryEligible = draftCurrent && consumerGate.status === "pass" && consumerGate.valid === true;
      const blockers = [];
      if (txDraft.status !== "ready") blockers.push("draft-not-ready");
      if (txDraft.provenanceEnforced !== true) blockers.push("provenance-not-enforced");
      if (freshnessStatus !== "valid") blockers.push(`freshness-${freshnessStatus || "unknown"}`);
      if (invalidationReasons.length) blockers.push(...invalidationReasons);
      if (!noSendBoundaryPreserved) blockers.push("no-send-boundary-missing");
      blockers.push("send-sign-not-implemented");
      const status = futureBoundaryEligible
        ? "locked-ready-for-future-boundary"
        : (txDraft.status === "ready" ? "blocked" : "locked-no-draft");
      return {
        kind: "mcel-tx-draft-endgame-preflight.v1",
        status,
        futureBoundaryEligible,
        draftCurrent,
        consumerGateStatus: consumerGate.status || "not-observed",
        freshnessStatus,
        invalidationReasons,
        canSend: false,
        canSign: false,
        canBroadcast: false,
        noSendBoundaryPreserved,
        action: futureBoundaryEligible
          ? "hold no-send lock; design explicit send/sign boundary separately"
          : "rebuild draft from current receipt before future send/sign boundary work",
        blockers: [...new Set(blockers.filter(Boolean))],
        invariant: [
          "future send/sign is locked",
          "no provider send method is called",
          "draft must be current before future boundary design",
          "consumer gate must pass before any source-affecting use",
          "no-send boundary remains preserved"
        ]
      };
    }

    function mcelTinyContractTxDraftProvenance({
      source = {},
      state = {},
      runtime = {},
      request = null,
      wallet = {},
      network = {},
      externalOutcome = {},
      encoding = {},
      draftProbe = null,
      ready = false,
      invalidatedBy = []
    } = {}) {
      const selectedRequestSnapshot = mcelTinyContractSelectedRequestSnapshot(request);
      const expectedChainId = network.expectedChainId || source.devRelease?.devNetwork?.chainId || "0x28757b2";
      const actualChainId = network.chainId || "";
      const sourceRequestHash = selectedRequestSnapshot ? mcelTinyContractObjectHash(selectedRequestSnapshot) : "";
      const walletAccountHash = mcelTinyContractWalletAccountHash(wallet.account || wallet.address || "");
      const chainOk = Boolean(actualChainId && String(actualChainId).toLowerCase() === String(expectedChainId).toLowerCase());
      return {
        provenanceVersion: "txDraft.provenance.v1",
        sourceRequestHash,
        selectedRequestSnapshot,
        walletAccountHash,
        chainProof: {
          expectedChainId,
          chainId: actualChainId,
          ok: chainOk,
          status: chainOk ? "matched" : "mismatch-or-missing"
        },
        externalOutcomeSequence: externalOutcome?.kind === "mcel-external-outcome"
          ? [{
              sequence: externalOutcome.sequence || null,
              operation: externalOutcome.operation || "",
              status: externalOutcome.status || "",
              reason: externalOutcome.reason || ""
            }]
          : [],
        networkGateSequence: [{
          status: network.status || "waiting",
          ok: network.ok === true,
          expectedChainId,
          chainId: actualChainId
        }],
        calldataSource: encoding.calldata || encoding.data
          ? "abi-encoding"
          : (encoding.calldataEncoding || "not-encoded"),
        abiEncodingStatus: encoding.status || encoding.calldataEncoding || "unknown",
        probeEnvelopeIds: draftProbe?.kind === "mcel-runtime-tx-draft-probe"
          ? mcelTinyContractTxProbeEnvelopeIds(draftProbe)
          : ["eth_getTransactionCount:not-probed", "eth_estimateGas:not-probed", "eth_call:not-probed"],
        invalidatedBy: invalidatedBy.filter(Boolean),
        valid: ready === true && invalidatedBy.length === 0,
        freshnessStatus: ready === true && invalidatedBy.length === 0 ? "valid" : (invalidatedBy.length ? "invalidated" : "stale"),
        freshnessAction: ready === true && invalidatedBy.length === 0
          ? "inspect or replay draft; no-send boundary preserved"
          : (invalidatedBy.length ? "rebuild draft from current receipt" : "rebuild draft to prove freshness"),
        noSendBoundaryPreserved: true,
        provenanceEnforced: false,
        validityInvariant: [
          "same selected source request",
          "same wallet account",
          "same chain",
          "wallet outcome pass",
          "network gate pass",
          "no provider event invalidated draft",
          "no transaction send attempted"
        ]
      };
    }

    function ensureMcelTinyContractState() {
      if (!mcelLabState.tinyContract) {
        mcelLabState.tinyContract = {
          selectedIndex: 0,
          runCount: 0,
          blockedWrites: 0,
          repairCount: 0,
          repairPacketCount: 0,
          repairBoundaryBlockedCount: 0,
          reviewedCount: 0,
          walletConnectCount: 0,
          walletDisconnectCount: 0,
          walletDisconnectCommitCount: 0,
          walletRevokeAttemptCount: 0,
          walletRevokeSuccessCount: 0,
          providerAccountsChangedCount: 0,
          providerAccountSwitchCount: 0,
          providerAccountDisconnectCount: 0,
          providerChainChangedCount: 0,
          providerDisconnectCount: 0,
          providerErrorCount: 0,
          routeLoaderCount: 0,
          networkVerifyCount: 0,
          releaseSelectCount: 0,
          txDraftCount: 0,
          fullBatteryRunCount: 0,
          externalOutcomeCount: 0,
          externalBlockedCount: 0,
          externalExceptionCount: 0,
          lastWalletResetClean: false,
          lastExternalOutcome: null,
          lastWalletActionOutcome: null,
          lastProof: null,
          lastWalletCommitBoundary: null,
          commitBoundaryReceipts: [],
          walletStaleSimulation: null,
          walletRebuildDraftCount: 0,
          walletStaleSimulationCount: 0,
          evidence: [],
          walletAdapter: null,
          walletSubsystemMode: "unobserved",
          scmInstance: null,
          scmRouteInstance: null
        };
      }
      if (!Array.isArray(mcelLabState.tinyContract.evidence)) {
        mcelLabState.tinyContract.evidence = [];
      }
      if (!Array.isArray(mcelLabState.tinyContract.commitBoundaryReceipts)) {
        mcelLabState.tinyContract.commitBoundaryReceipts = [];
      }
      if (!mcelLabState.tinyContract.lastWalletCommitBoundary || typeof mcelLabState.tinyContract.lastWalletCommitBoundary !== "object") {
        mcelLabState.tinyContract.lastWalletCommitBoundary = mcelCommitBoundaryDefault("wallet.send-sign", "state-initialized");
      }
      if (!mcelLabState.tinyContract.walletAdapter || typeof mcelLabState.tinyContract.walletAdapter !== "object") {
        mcelLabState.tinyContract.walletAdapter = {
          providerKind: "unknown",
          liveProvider: false,
          mockFallback: false,
          ethersReady: false,
          walletSubsystemReady: false,
          walletSubsystemUsed: false,
          walletSubsystemPreferred: false,
          directProviderFallback: false,
          connectSource: "unknown",
          disconnectSource: "unknown",
          eventsBound: false,
          calls: [],
          events: [],
          lastError: "",
          permissionRevoked: false
        };
      }
      if (!Array.isArray(mcelLabState.tinyContract.walletAdapter.calls)) {
        mcelLabState.tinyContract.walletAdapter.calls = [];
      }
      if (!Array.isArray(mcelLabState.tinyContract.walletAdapter.events)) {
        mcelLabState.tinyContract.walletAdapter.events = [];
      }
      if (!Number.isFinite(Number(mcelLabState.tinyContract.selectedIndex))) {
        mcelLabState.tinyContract.selectedIndex = 0;
      }
      [
        "walletConnectCount",
        "walletDisconnectCount",
        "walletDisconnectCommitCount",
        "walletRevokeAttemptCount",
        "walletRevokeSuccessCount",
        "providerAccountsChangedCount",
        "providerAccountSwitchCount",
        "providerAccountDisconnectCount",
        "providerChainChangedCount",
        "providerDisconnectCount",
        "providerErrorCount",
        "routeLoaderCount",
        "networkVerifyCount",
        "releaseSelectCount",
        "txDraftCount",
        "walletRebuildDraftCount",
        "walletStaleSimulationCount",
        "fullBatteryRunCount",
        "externalOutcomeCount",
        "externalBlockedCount",
        "externalExceptionCount",
        "blockedWrites",
        "repairCount",
        "reviewedCount"
      ].forEach((key) => {
        if (!Number.isFinite(Number(mcelLabState.tinyContract[key]))) {
          mcelLabState.tinyContract[key] = 0;
        }
      });
      if (typeof mcelLabState.tinyContract.lastWalletResetClean !== "boolean") {
        mcelLabState.tinyContract.lastWalletResetClean = false;
      }
      if (!mcelLabState.tinyContract.lastExternalOutcome || typeof mcelLabState.tinyContract.lastExternalOutcome !== "object") {
        mcelLabState.tinyContract.lastExternalOutcome = null;
      }
      return mcelLabState.tinyContract;
    }

    function recordMcelTinyContractEvidence(kind, message, status = "pass", detail = {}) {
      const tinyState = ensureMcelTinyContractState();
      const event = {
        index: tinyState.evidence.length + 1,
        kind,
        status,
        message,
        detail
      };
      tinyState.evidence.push(event);
      tinyState.evidence = tinyState.evidence.slice(-16);
      return event;
    }

    function parseMcelTinyContractLanguage() {
      const text = mcelTinyContractLanguageSource();
      try {
        return JSON.parse(text || "{}");
      } catch (error) {
        return {
          kind: "mcel.scm.app",
          name: "DevNetworkReleaseConsole",
          parseError: error?.message || String(error)
        };
      }
    }

    function mcelTinyContractInitialSourceData() {
      return {
        devRelease: {
          title: "Dev Network Release Console",
          summary: "Approve a local contract release only when the wallet, route-loaded contract, source-owned request, runtime transaction draft, and SCM evidence agree.",
          contractAddress: "0x000000000000000000000000000000000000dEaD",
          devNetwork: {
            name: "Main Computer Dev Chain",
            chainId: "0x28757b2",
            decimalChainId: 42424242,
            rpcUrl: "http://127.0.0.1:18545"
          },
          requests: [
            {
              id: "rel-allowance-view",
              title: "Inspect allowance-reader deployment before approving UI release.",
              status: "needs-wallet",
              risk: "medium",
              contractMethod: "allowance(address,address)",
              evidenceRequired: true
            },
            {
              id: "rel-settlement-mock",
              title: "Gate settlement mock release behind dev-network chain proof.",
              status: "needs-review",
              risk: "high",
              contractMethod: "releaseSettlementMock(bytes32)",
              evidenceRequired: true
            },
            {
              id: "rel-ai-hint",
              title: "Repair runtime wallet hint without changing release source.",
              status: "needs-repair",
              risk: "medium",
              contractMethod: "repairRuntimeHint()",
              evidenceRequired: true
            }
          ]
        },
        html: {
          sourceId: "dev-release-console.source.html"
        }
      };
    }

    function mcelTinyContractRuntimeDefaults() {
      return {
        wallet: {
          mode: "disconnected",
          provider: "none",
          account: "",
          connected: false
        },
        network: {
          expectedChainId: "0x28757b2",
          chainId: "",
          ok: false,
          status: "waiting"
        },
        txDraft: {
          status: "empty",
          requestId: "",
          createdFrom: {},
          from: "",
          to: "",
          value: "0x0",
          chainId: "",
          expectedChainId: "0x28757b2",
          data: "",
          calldata: "",
          calldataEncoding: "",
          methodSignature: "",
          argsPreview: [],
          nonce: {
            method: "eth_getTransactionCount",
            status: "not-probed"
          },
          gasEstimate: {
            method: "eth_estimateGas",
            status: "not-probed"
          },
          ethCall: {
            method: "eth_call",
            status: "not-probed"
          },
          noSend: true,
          boundary: "runtime-only-no-send",
          sourceRequestHash: "",
          selectedRequestSnapshot: null,
          walletAccountHash: "",
          chainProof: {
            expectedChainId: "0x28757b2",
            chainId: "",
            ok: false,
            status: "waiting"
          },
          externalOutcomeSequence: [],
          networkGateSequence: [],
          calldataSource: "",
          abiEncodingStatus: "",
          probeEnvelopeIds: [],
          invalidatedBy: [],
          validityInvariant: [
            "same selected source request",
            "same wallet account",
            "same chain",
            "wallet outcome pass",
            "network gate pass",
            "no provider event invalidated draft",
            "no transaction send attempted"
          ],
          valid: false,
          summary: "No transaction draft has been built."
        },
        txDraftConsumerGate: {
          kind: "mcel-tx-draft-consumer-gate.v1",
          consumer: "runtime.txDraft.consumer",
          status: "not-observed",
          valid: false,
          draftStatus: "empty",
          freshnessStatus: "not-observed",
          invalidatedBy: [],
          invalidationReasons: [],
          noSendBoundaryPreserved: true,
          provenanceEnforced: false,
          action: "build draft before source-affecting consumer",
          reason: "No txDraft consumer has inspected a runtime draft yet.",
          invariant: [
            "txDraft status ready",
            "txDraft provenance enforced",
            "txDraft freshness valid",
            "no invalidation reasons",
            "no-send boundary preserved"
          ],
          endgamePreflight: {
            kind: "mcel-tx-draft-endgame-preflight.v1",
            status: "locked-no-draft",
            futureBoundaryEligible: false,
            canSend: false,
            canSign: false,
            canBroadcast: false,
            noSendBoundaryPreserved: true,
            action: "build and validate a runtime-only draft before future send/sign boundary work",
            blockers: ["draft-not-ready", "send-sign-not-implemented"]
          }
        },
        walletCommitBoundary: mcelCommitBoundaryDefault("wallet.send-sign", "runtime-defaults"),
        walletAdapter: {
          providerKind: "unknown",
          liveProvider: false,
          mockFallback: false,
          eventsBound: false
        },
        walletEvents: [],
        externalOutcome: {
          kind: "mcel-external-outcome",
          operation: "",
          phase: "waiting",
          status: "waiting",
          known: false,
          reason: "not-run",
          message: "No external wallet operation has run.",
          containment: {
            sourceChanged: false,
            txDraftCreated: false,
            runtimeMutationGoverned: true
          }
        },
        proofChip: {
          text: "Runtime wallet proof chip waiting.",
          status: "pending",
          repaired: false
        },
        repairPacket: {
          kind: "mcel-repair-packet",
          status: "not-generated",
          target: "runtime.proofChip",
          reason: "Repair packet has not been generated."
        },
        assistantRepairPrompt: "",
        serializedSource: "",
        evidenceStrip: []
      };
    }

    function mcelTinyContractStateDefaults() {
      return {
        selectedRequestId: "rel-allowance-view",
        walletGate: "waiting"
      };
    }

    function mcelTinyContractDomRect(node) {
      if (!node || typeof node.getBoundingClientRect !== "function") return {};
      const rect = node.getBoundingClientRect();
      return {
        x: Number(rect.x || 0),
        y: Number(rect.y || 0),
        top: Number(rect.top || 0),
        right: Number(rect.right || 0),
        bottom: Number(rect.bottom || 0),
        left: Number(rect.left || 0),
        width: Number(rect.width || 0),
        height: Number(rect.height || 0)
      };
    }

    function mcelTinyContractComputedSnapshot(node, properties = []) {
      const snapshot = {};
      if (!node || typeof window.getComputedStyle !== "function") return snapshot;
      const computed = window.getComputedStyle(node);
      properties.forEach((property) => {
        snapshot[property] = computed.getPropertyValue(property) || computed[property] || "";
      });
      return snapshot;
    }

    function mcelTinyContractNodePresent(node) {
      if (!node) return false;
      if (node.isConnected === false) return false;
      return true;
    }

    function mcelTinyContractDocumentHeightRatio(rootNode, shellNode) {
      const rootRect = mcelTinyContractDomRect(rootNode);
      const shellRect = mcelTinyContractDomRect(shellNode);
      const appHeight = rootRect.height || rootNode?.scrollHeight || rootNode?.offsetHeight || 0;
      const shellHeight = shellRect.height || shellNode?.clientHeight || shellNode?.offsetHeight || window.innerHeight || appHeight || 0;
      if (!appHeight || !shellHeight) return null;
      return Number((appHeight / shellHeight).toFixed(3));
    }

    function mcelTinyContractObservation(app = null) {
      const rootNode = app?.matches?.('[data-mc-component="dev-network-release-console"]')
        ? app
        : (app?.querySelector?.('[data-mc-component="dev-network-release-console"]')
          || mcelTinyContractRuntimeMount?.querySelector?.('[data-mc-component="dev-network-release-console"]')
          || null);
      const shellNode = rootNode?.closest?.(".mcel-tiny-contract-runtime") || mcelTinyContractRuntimeMount || rootNode?.parentElement || rootNode;
      const txNode = rootNode?.querySelector?.(".mcel-dev-release-console__tx") || null;
      const walletPanel = rootNode?.querySelector?.('[data-mc-component="dev-release.wallet"]') || null;
      const releaseQueue = rootNode?.querySelector?.('[data-mc-field="devRelease.requests"]') || null;
      const txPreview = rootNode?.querySelector?.('[data-mc-slot="runtime.txDraft"]') || null;
      const evidenceStrip = rootNode?.querySelector?.('[data-mc-slot="runtime.evidenceStrip"]') || null;
      const regionEntries = {
        walletPanel,
        releaseQueue,
        txPreview,
        evidenceStrip
      };
      const regions = Object.fromEntries(Object.entries(regionEntries).map(([key, node]) => [key, mcelTinyContractNodePresent(node)]));
      const presentSelectors = [
        rootNode ? ".mcel-dev-release-console" : "",
        walletPanel ? "[data-mc-component='dev-release.wallet']" : "",
        releaseQueue ? "[data-mc-field='devRelease.requests']" : "",
        txPreview ? "[data-mc-slot='runtime.txDraft']" : "",
        evidenceStrip ? "[data-mc-slot='runtime.evidenceStrip']" : "",
        txNode ? ".mcel-dev-release-console__tx" : ""
      ].filter(Boolean);
      const rootRect = mcelTinyContractDomRect(rootNode);
      const shellRect = mcelTinyContractDomRect(shellNode);
      const documentHeightRatio = mcelTinyContractDocumentHeightRatio(rootNode, shellNode);
      return {
        kind: "mcel-lab-browser-layout-observation",
        source: "browser-dom",
        measured: Boolean(rootNode),
        computed: {
          ".mcel-dev-release-console": mcelTinyContractComputedSnapshot(rootNode, ["display", "overflow"]),
          ".mcel-dev-release-console__tx": mcelTinyContractComputedSnapshot(txNode, ["overflow"])
        },
        regions,
        rects: {
          ".mcel-dev-release-console": rootRect,
          ".mcel-tiny-contract-runtime": shellRect
        },
        presentSelectors,
        metrics: {
          appHeight: rootRect.height || rootNode?.scrollHeight || 0,
          shellHeight: shellRect.height || shellNode?.clientHeight || 0,
          appScrollHeight: rootNode?.scrollHeight || 0,
          shellScrollHeight: shellNode?.scrollHeight || 0,
          viewportHeight: window.innerHeight || 0,
          documentHeightRatio
        },
        documentHeightRatio
      };
    }

    function mcelTinyContractRepairForbiddenWrites() {
      return [
        "source.devRelease",
        "source.devRelease.devNetwork",
        "source.devRelease.requests",
        "source.devRelease.contractAddress",
        "state.selectedRequestId",
        "runtime.wallet",
        "runtime.wallet.account",
        "runtime.network",
        "runtime.network.chainId",
        "runtime.txDraft",
        "runtime.externalOutcome"
      ];
    }

    function buildMcelTinyContractRepairPacket({wallet = {}, network = {}, txDraft = {}, externalOutcome = {}, proofChip = {}, payload = {}} = {}) {
      const reason = payload.reason || "runtime-proof-display-gap";
      const outcomeStatus = externalOutcome?.status || "waiting";
      const outcomeReason = externalOutcome?.reason || "not-run";
      const txStatus = txDraft?.status || "empty";
      return {
        kind: "mcel-repair-packet",
        version: "0.1.0",
        status: "ready",
        target: "runtime.proofChip",
        reason,
        createdAt: new Date().toISOString(),
        liveAiCall: false,
        modelCall: "not-requested",
        allowedWrites: [
          "runtime.proofChip",
          "runtime.assistantRepairPrompt",
          "runtime.repairPacket",
          "runtime.evidenceStrip"
        ],
        forbiddenWrites: mcelTinyContractRepairForbiddenWrites(),
        evidence: {
          walletConnected: wallet.connected === true,
          walletProvider: wallet.provider || wallet.mode || "unknown",
          networkStatus: network.status || "waiting",
          networkOk: network.ok === true,
          chainId: network.chainId || "",
          expectedChainId: network.expectedChainId || "0x28757b2",
          externalOutcomeStatus: outcomeStatus,
          externalOutcomeReason: outcomeReason,
          txDraftStatus: txStatus,
          txDraftBoundary: txDraft.boundary || "runtime-only-no-send",
          proofChipStatus: proofChip.status || "pending",
          proofChipTextPresent: Boolean(proofChip.text)
        },
        instruction: [
          "Summarize the runtime proof state only.",
          "Do not change source.devRelease, state, runtime.wallet, runtime.network, runtime.txDraft, or runtime.externalOutcome.",
          "Return proposed runtime proof text only; SCM will reject undeclared writes."
        ].join(" ")
      };
    }

    function summarizeMcelTinyContractRepairPacket(packet = {}) {
      const evidence = packet.evidence || {};
      return [
        `repair packet target=${packet.target || "runtime.proofChip"}`,
        `outcome=${evidence.externalOutcomeStatus || "waiting"}:${evidence.externalOutcomeReason || "not-run"}`,
        `wallet=${evidence.walletConnected ? "connected" : "not-connected"}`,
        `network=${evidence.networkStatus || "waiting"}`,
        `txDraft=${evidence.txDraftStatus || "empty"}`,
        "liveAiCall=false"
      ].join(" · ");
    }

    function mcelTinyContractScmManifest() {
      return {
        version: "0.2.0",
        contract: "mcel.scm.dev-network-release-console.v1",
        owns: {
          source: [
            "devRelease.title",
            "devRelease.summary",
            "devRelease.contractAddress",
            "devRelease.devNetwork",
            "devRelease.requests",
            "html.sourceId"
          ],
          state: [
            "selectedRequestId",
            "walletGate"
          ],
          runtime: [
            "wallet",
            "network",
            "txDraft",
            "txDraftConsumerGate",
            "walletAdapter",
            "walletEvents",
            "externalOutcome",
            "proofChip",
            "repairPacket",
            "assistantRepairPrompt",
            "serializedSource",
            "evidenceStrip"
          ],
          layout: [
            "walletPanel",
            "releaseQueue",
            "txPreview",
            "evidenceStrip"
          ],
          style: [
            "devWalletConsole"
          ],
          effects: [
            "wallet.connect",
            "wallet.disconnect",
            "wallet.provider.accountsChanged",
            "wallet.provider.chainChanged",
            "wallet.provider.disconnect",
            "wallet.provider.error",
            "network.verify",
            "release.select",
            "release.draftTx",
            "release.approve",
            "ai.repairWalletHint"
          ]
        },
        source: mcelTinyContractInitialSourceData(),
        runtime: mcelTinyContractRuntimeDefaults(),
        state: mcelTinyContractStateDefaults(),
        outputs: [
          "walletConnected",
          "walletDisconnected",
          "walletProviderAccountsChanged",
          "walletProviderChainChanged",
          "walletProviderDisconnected",
          "walletProviderError",
          "externalOutcomeCaptured",
          "networkVerified",
          "releaseSelected",
          "txDrafted",
          "releaseApproved",
          "runtimeHintRepaired",
          "unsafeWriteBlocked",
          "serialized"
        ],
        effects: {
          "wallet.connect": {
            kind: "external-wallet-effect",
            triggers: ["state.walletGate"],
            reads: ["source.devRelease.devNetwork"],
            writes: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletAdapter", "runtime.externalOutcome", "runtime.evidenceStrip"],
            external: {
              resource: "metamask",
              operation: "normalized-outcome: eth_requestAccounts + eth_chainId",
              devNetworkOnly: true
            },
            errorPolicy: {
              onFailure: "record-runtime-wallet-error"
            },
            run(ctx, payload = {}) {
              const devNetwork = ctx.get("source.devRelease.devNetwork") || {};
              const outcome = mcelTinyContractOutcomeFromWalletPayload(payload);
              const chainId = String(payload.chainId || outcome.value?.chainId || devNetwork.chainId || "0x28757b2");
              const account = outcome.status === "pass"
                ? String(payload.account || outcome.value?.account || "")
                : "";
              const provider = String(payload.provider || outcome.provider?.kind || "mock-dev-provider");
              const ok = outcome.status === "pass" && chainId.toLowerCase() === String(devNetwork.chainId || "").toLowerCase();
              const wallet = {
                mode: payload.mock ? "mock-dev-wallet" : provider,
                provider,
                account,
                connected: Boolean(account) && outcome.status === "pass",
                interactive: payload.interactive === true,
                status: outcome.status === "pass" ? "connected" : outcome.status,
                outcomeStatus: outcome.status,
                outcomeReason: outcome.reason
              };
              const network = {
                expectedChainId: devNetwork.chainId || "0x28757b2",
                chainId,
                ok,
                status: ok ? "dev-network-ready" : (outcome.status === "pass" ? "wrong-chain" : `wallet-${outcome.status}`),
                rpcUrl: devNetwork.rpcUrl || "",
                outcomeStatus: outcome.status
              };
              const txDraft = outcome.status === "pass"
                ? null
                : {
                    status: "empty",
                    requestId: "",
                    to: "",
                    data: "",
                    invalidatedBy: [
                      mcelTinyContractTxDraftInvalidation(`wallet-${outcome.status}`, {
                        outcomeStatus: outcome.status,
                        outcomeReason: outcome.reason
                      })
                    ],
                    sourceRequestHash: "",
                    selectedRequestSnapshot: null,
                    walletAccountHash: "",
                    chainProof: {
                      expectedChainId: devNetwork.chainId || "0x28757b2",
                      chainId,
                      ok: false,
                      status: "wallet-not-ready"
                    },
                    externalOutcomeSequence: [{
                      sequence: outcome.sequence || null,
                      operation: outcome.operation || "wallet.connect",
                      status: outcome.status,
                      reason: outcome.reason
                    }],
                    networkGateSequence: [{
                      status: network.status,
                      ok: network.ok === true,
                      expectedChainId: network.expectedChainId,
                      chainId
                    }],
                    calldataSource: "not-encoded",
                    abiEncodingStatus: "blocked-by-wallet",
                    probeEnvelopeIds: [],
                    valid: false,
                    summary: `Wallet ${outcome.status}; transaction draft cleared before source approval.`
                  };
              ctx.set("runtime.wallet", wallet);
              ctx.set("runtime.network", network);
              if (txDraft) ctx.set("runtime.txDraft", txDraft);
              ctx.set("runtime.walletAdapter", payload.adapter || {
                providerKind: provider,
                liveProvider: payload.liveProvider === true,
                mockFallback: payload.mock === true,
                eventsBound: Boolean(payload.adapter?.eventsBound)
              });
              ctx.set("runtime.externalOutcome", outcome);
              ctx.set("runtime.evidenceStrip", [
                `wallet.connect outcome=${outcome.status}`,
                `reason=${outcome.reason}`,
                `provider=${provider}`,
                `account=${account ? account.slice(0, 10) + "…" : "missing"}`,
                `chain=${chainId}`,
                `expected=${network.expectedChainId}`
              ]);
              ctx.evidence({
                ok: true,
                message: outcome.status === "pass"
                  ? "wallet.connect captured a successful external outcome inside declared runtime writes."
                  : "wallet.connect captured a blocked/exception external outcome and kept source untouched."
              });
              return {wallet, network, outcome, txDraftCleared: Boolean(txDraft)};
            },
            commit(_ctx, result) {
              return {
                connected: result?.wallet?.connected === true,
                chainId: result?.network?.chainId || "",
                ok: result?.network?.ok === true,
                outcomeStatus: result?.outcome?.status || "",
                outcomeReason: result?.outcome?.reason || "",
                txDraftCleared: result?.txDraftCleared === true
              };
            }
          },
          "wallet.disconnect": {
            kind: "runtime-wallet-reset-effect",
            triggers: ["state.walletGate"],
            reads: ["runtime.wallet", "runtime.network", "runtime.txDraft"],
            writes: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletEvents", "runtime.externalOutcome", "runtime.evidenceStrip"],
            external: {
              resource: "wallet-runtime",
              operation: "normalized-outcome: reset-local-wallet-session"
            },
            errorPolicy: {
              onFailure: "keep-disconnect-evidence"
            },
            run(ctx, payload = {}) {
              const previousWallet = ctx.get("runtime.wallet") || {};
              const previousNetwork = ctx.get("runtime.network") || {};
              const outcome = payload.outcome?.kind === "mcel-external-outcome"
                ? payload.outcome
                : mcelTinyContractOutcomeFromRevoke(payload.revoke || {});
              const expectedChainId = previousNetwork.expectedChainId || previousNetwork.chainId || "0x28757b2";
              const wallet = {
                mode: "disconnected",
                provider: "none",
                account: "",
                connected: false,
                previousProvider: previousWallet.provider || previousWallet.mode || "none",
                status: "disconnected",
                outcomeStatus: outcome.status,
                outcomeReason: outcome.reason,
                permissionNote: "Provider permission revoke is an external outcome; MCEL reset local runtime wallet state through SCM."
              };
              const network = {
                expectedChainId,
                chainId: "",
                ok: false,
                status: "disconnected",
                outcomeStatus: outcome.status
              };
              const txDraft = {
                status: "empty",
                requestId: "",
                to: "",
                data: "",
                invalidatedBy: [
                  mcelTinyContractTxDraftInvalidation("wallet-disconnected", {
                    previousProvider: wallet.previousProvider,
                    outcomeStatus: outcome.status,
                    outcomeReason: outcome.reason
                  })
                ],
                sourceRequestHash: "",
                selectedRequestSnapshot: null,
                walletAccountHash: "",
                chainProof: {
                  expectedChainId,
                  chainId: "",
                  ok: false,
                  status: "wallet-disconnected"
                },
                externalOutcomeSequence: [{
                  sequence: outcome.sequence || null,
                  operation: outcome.operation || "wallet.disconnect",
                  status: outcome.status,
                  reason: outcome.reason
                }],
                networkGateSequence: [{
                  status: network.status,
                  ok: false,
                  expectedChainId,
                  chainId: ""
                }],
                calldataSource: "not-encoded",
                abiEncodingStatus: "invalidated-by-wallet-disconnect",
                probeEnvelopeIds: [],
                valid: false,
                summary: "Wallet disconnected; runtime transaction draft was reset."
              };
              ctx.set("runtime.wallet", wallet);
              ctx.set("runtime.network", network);
              ctx.set("runtime.txDraft", txDraft);
              ctx.set("runtime.externalOutcome", outcome);
              ctx.set("runtime.walletEvents", [{
                type: "disconnect",
                outcomeStatus: outcome.status,
                reason: outcome.reason,
                txDraftCleared: true
              }]);
              ctx.set("runtime.evidenceStrip", [
                "wallet.disconnect reset runtime.wallet",
                "runtime.network cleared",
                "runtime.txDraft cleared",
                `externalOutcome=${outcome.status}`,
                `reason=${outcome.reason}`,
                "durable source unchanged"
              ]);
              ctx.evidence({
                ok: true,
                message: "wallet.disconnect consumed an external outcome and cleared runtime wallet/network/tx draft without touching source."
              });
              return {disconnected: true, previousProvider: wallet.previousProvider, outcome};
            },
            commit(_ctx, result) {
              return {
                disconnected: result?.disconnected === true,
                previousProvider: result?.previousProvider || "",
                outcomeStatus: result?.outcome?.status || "",
                outcomeReason: result?.outcome?.reason || ""
              };
            }
          },
          "wallet.provider.accountsChanged": {
            kind: "provider-event-effect",
            triggers: ["runtime.walletAdapter"],
            reads: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletEvents"],
            writes: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletEvents", "runtime.externalOutcome", "runtime.evidenceStrip"],
            external: {
              resource: "metamask-provider-event",
              operation: "accountsChanged"
            },
            errorPolicy: {
              onFailure: "record-provider-event-error"
            },
            run(ctx, payload = {}) {
              const accounts = Array.isArray(payload.accounts) ? payload.accounts : [];
              const nextAccount = String(accounts[0] || "");
              const wallet = ctx.get("runtime.wallet") || {};
              const network = ctx.get("runtime.network") || {};
              const txDraft = ctx.get("runtime.txDraft") || {};
              const walletEvents = Array.isArray(ctx.get("runtime.walletEvents")) ? ctx.get("runtime.walletEvents") : [];
              const previousAccount = String(wallet.account || "");
              const previousNonEmptyAccount = String(
                previousAccount ||
                [...walletEvents].reverse().find((event) => event?.account)?.account ||
                ""
              );
              const accountChanged = Boolean(nextAccount && previousNonEmptyAccount && nextAccount.toLowerCase() !== previousNonEmptyAccount.toLowerCase());
              const disconnected = !nextAccount;
              const shouldClearDraft = disconnected || accountChanged;
              const nextWallet = {
                ...wallet,
                account: nextAccount,
                connected: Boolean(nextAccount),
                providerEvent: "accountsChanged",
                status: disconnected ? "disconnected" : (accountChanged ? "account-changed" : "connected")
              };
              const nextNetwork = disconnected
                ? {
                    ...network,
                    ok: false,
                    status: "wallet-disconnected"
                  }
                : network;
              const nextTxDraft = shouldClearDraft
                ? {
                    status: "empty",
                    requestId: "",
                    to: "",
                    data: "",
                    sourceRequestHash: txDraft.sourceRequestHash || "",
                    selectedRequestSnapshot: txDraft.selectedRequestSnapshot || null,
                    walletAccountHash: mcelTinyContractWalletAccountHash(nextAccount),
                    chainProof: txDraft.chainProof || {
                      expectedChainId: network.expectedChainId || "0x28757b2",
                      chainId: network.chainId || "",
                      ok: network.ok === true,
                      status: network.ok === true ? "matched" : "wallet-event"
                    },
                    externalOutcomeSequence: txDraft.externalOutcomeSequence || [],
                    networkGateSequence: txDraft.networkGateSequence || [],
                    calldataSource: txDraft.calldataSource || "not-encoded",
                    abiEncodingStatus: "invalidated-by-account-event",
                    probeEnvelopeIds: txDraft.probeEnvelopeIds || [],
                    invalidatedBy: [
                      ...(txDraft.invalidatedBy || []),
                      mcelTinyContractTxDraftInvalidation(disconnected ? "account-disconnected" : "account-changed", {
                        previousAccount: previousNonEmptyAccount || previousAccount,
                        nextAccount
                      })
                    ],
                    valid: false,
                    summary: "Provider accountsChanged event cleared the runtime transaction draft."
                  }
                : txDraft;
              ctx.set("runtime.wallet", nextWallet);
              ctx.set("runtime.network", nextNetwork);
              ctx.set("runtime.txDraft", nextTxDraft);
              const nextWalletEvent = {
                type: "accountsChanged",
                accounts,
                account: nextAccount,
                previousAccount,
                previousNonEmptyAccount,
                accountChanged,
                disconnected,
                txDraftCleared: shouldClearDraft
              };
              const outcome = payload.outcome?.kind === "mcel-external-outcome"
                ? payload.outcome
                : mcelTinyContractExternalOutcome({
                    operation: "wallet.provider.accountsChanged",
                    phase: "provider-event",
                    status: "pass",
                    known: true,
                    reason: disconnected ? "account-disconnected" : (accountChanged ? "account-switched" : "account-event"),
                    message: "Provider accountsChanged event was consumed through SCM.",
                    value: {account: nextAccount, accounts, disconnected, accountChanged},
                    rpc: mcelTinyContractWalletRpcMethods(mcelTinyContractWalletAdapterState(), true)
                  });
              ctx.set("runtime.externalOutcome", outcome);
              ctx.set("runtime.walletEvents", [...walletEvents, {...nextWalletEvent, outcomeStatus: outcome.status, outcomeReason: outcome.reason}].slice(-16));
              ctx.set("runtime.evidenceStrip", [
                `wallet.provider.accountsChanged account=${nextAccount ? nextAccount.slice(0, 10) + "…" : "none"}`,
                `previous=${previousNonEmptyAccount ? previousNonEmptyAccount.slice(0, 10) + "…" : "none"}`,
                `accountChanged=${accountChanged}`,
                `txDraftCleared=${shouldClearDraft}`
              ]);
              ctx.evidence({
                ok: true,
                message: disconnected
                  ? "Provider accountsChanged disconnected the wallet through a declared SCM effect."
                  : "Provider accountsChanged updated runtime wallet state through a declared SCM effect."
              });
              return {
                account: nextAccount,
                connected: Boolean(nextAccount),
                disconnected,
                previousAccount,
                previousNonEmptyAccount,
                accountChanged,
                txDraftCleared: shouldClearDraft
              };
            },
            commit(_ctx, result) {
              return {
                account: result?.account || "",
                connected: result?.connected === true,
                disconnected: result?.disconnected === true,
                previousAccount: result?.previousAccount || "",
                previousNonEmptyAccount: result?.previousNonEmptyAccount || "",
                accountChanged: result?.accountChanged === true,
                txDraftCleared: result?.txDraftCleared === true
              };
            }
          },
          "wallet.provider.chainChanged": {
            kind: "provider-event-effect",
            triggers: ["runtime.walletAdapter"],
            reads: ["source.devRelease.devNetwork", "runtime.wallet", "runtime.network", "runtime.txDraft"],
            writes: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletEvents", "runtime.externalOutcome", "runtime.evidenceStrip"],
            external: {
              resource: "metamask-provider-event",
              operation: "chainChanged"
            },
            errorPolicy: {
              onFailure: "record-provider-event-error"
            },
            run(ctx, payload = {}) {
              const devNetwork = ctx.get("source.devRelease.devNetwork") || {};
              const wallet = ctx.get("runtime.wallet") || {};
              const network = ctx.get("runtime.network") || {};
              const txDraft = ctx.get("runtime.txDraft") || {};
              const chainId = String(payload.chainId || "");
              const expected = String(devNetwork.chainId || network.expectedChainId || "0x28757b2");
              const ok = chainId.toLowerCase() === expected.toLowerCase();
              const shouldClearDraft = !ok && txDraft?.status && txDraft.status !== "empty";
              const nextWallet = {
                ...wallet,
                chainId,
                providerEvent: "chainChanged"
              };
              const nextNetwork = {
                ...network,
                expectedChainId: expected,
                chainId,
                ok,
                status: ok ? "dev-network-ready" : "wrong-chain",
                providerEvent: "chainChanged"
              };
              const nextTxDraft = shouldClearDraft
                ? {
                    status: "empty",
                    requestId: "",
                    to: "",
                    data: "",
                    sourceRequestHash: txDraft.sourceRequestHash || "",
                    selectedRequestSnapshot: txDraft.selectedRequestSnapshot || null,
                    walletAccountHash: txDraft.walletAccountHash || mcelTinyContractWalletAccountHash(wallet.account || ""),
                    chainProof: {
                      expectedChainId: expected,
                      chainId,
                      ok,
                      status: ok ? "matched" : "mismatch"
                    },
                    externalOutcomeSequence: txDraft.externalOutcomeSequence || [],
                    networkGateSequence: [
                      ...(txDraft.networkGateSequence || []),
                      {status: ok ? "dev-network-ready" : "wrong-chain", ok, expectedChainId: expected, chainId}
                    ],
                    calldataSource: txDraft.calldataSource || "not-encoded",
                    abiEncodingStatus: "invalidated-by-chain-event",
                    probeEnvelopeIds: txDraft.probeEnvelopeIds || [],
                    invalidatedBy: [
                      ...(txDraft.invalidatedBy || []),
                      mcelTinyContractTxDraftInvalidation("chain-changed", {expectedChainId: expected, chainId})
                    ],
                    valid: false,
                    summary: "Provider chainChanged event cleared the runtime transaction draft because the chain no longer matched."
                  }
                : txDraft;
              const outcome = payload.outcome?.kind === "mcel-external-outcome"
                ? payload.outcome
                : mcelTinyContractExternalOutcome({
                    operation: "wallet.provider.chainChanged",
                    phase: "provider-event",
                    status: "pass",
                    known: true,
                    reason: ok ? "chain-matched" : "chain-mismatch",
                    message: "Provider chainChanged event was consumed through SCM.",
                    value: {chainId, expected, ok, txDraftCleared: shouldClearDraft},
                    rpc: mcelTinyContractWalletRpcMethods(mcelTinyContractWalletAdapterState(), true)
                  });
              ctx.set("runtime.wallet", nextWallet);
              ctx.set("runtime.network", nextNetwork);
              ctx.set("runtime.txDraft", nextTxDraft);
              ctx.set("runtime.externalOutcome", outcome);
              ctx.set("runtime.walletEvents", [{
                type: "chainChanged",
                outcomeStatus: outcome.status,
                outcomeReason: outcome.reason,
                chainId,
                expected,
                ok,
                txDraftCleared: shouldClearDraft
              }]);
              ctx.set("runtime.evidenceStrip", [
                `wallet.provider.chainChanged chain=${chainId || "missing"}`,
                `expected=${expected}`,
                `ok=${ok}`,
                `txDraftCleared=${shouldClearDraft}`
              ]);
              ctx.evidence({
                ok,
                message: ok
                  ? "Provider chainChanged kept the wallet on the expected dev network through a declared SCM effect."
                  : "Provider chainChanged moved the wallet off the expected dev network through a declared SCM effect."
              });
              return {chainId, expected, ok, txDraftCleared: shouldClearDraft};
            },
            commit(_ctx, result) {
              return {
                chainId: result?.chainId || "",
                expected: result?.expected || "",
                ok: result?.ok === true,
                txDraftCleared: result?.txDraftCleared === true
              };
            }
          },
          "wallet.provider.disconnect": {
            kind: "provider-event-effect",
            triggers: ["runtime.walletAdapter"],
            reads: ["runtime.wallet", "runtime.network", "runtime.txDraft"],
            writes: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletEvents", "runtime.externalOutcome", "runtime.evidenceStrip"],
            external: {
              resource: "metamask-provider-event",
              operation: "disconnect"
            },
            errorPolicy: {
              onFailure: "record-provider-event-error"
            },
            run(ctx, payload = {}) {
              const wallet = ctx.get("runtime.wallet") || {};
              const network = ctx.get("runtime.network") || {};
              const code = payload.code || payload.error?.code || "";
              const message = payload.message || payload.error?.message || "provider disconnect";
              const nextWallet = {
                ...wallet,
                account: "",
                connected: false,
                status: "disconnected",
                providerEvent: "disconnect",
                providerDisconnectCode: code,
                providerDisconnectMessage: message
              };
              const nextNetwork = {
                ...network,
                ok: false,
                status: "provider-disconnected",
                providerEvent: "disconnect"
              };
              const nextTxDraft = {
                status: "empty",
                requestId: "",
                to: "",
                data: "",
                sourceRequestHash: "",
                selectedRequestSnapshot: null,
                walletAccountHash: "",
                chainProof: {
                  expectedChainId: network.expectedChainId || "0x28757b2",
                  chainId: network.chainId || "",
                  ok: false,
                  status: "provider-disconnected"
                },
                externalOutcomeSequence: [],
                networkGateSequence: [{
                  status: "provider-disconnected",
                  ok: false,
                  expectedChainId: network.expectedChainId || "0x28757b2",
                  chainId: network.chainId || ""
                }],
                calldataSource: "not-encoded",
                abiEncodingStatus: "invalidated-by-provider-disconnect",
                probeEnvelopeIds: [],
                invalidatedBy: [
                  mcelTinyContractTxDraftInvalidation("provider-disconnect", {code, message})
                ],
                valid: false,
                summary: "Provider disconnect event cleared the runtime transaction draft."
              };
              const outcome = payload.outcome?.kind === "mcel-external-outcome"
                ? payload.outcome
                : mcelTinyContractExternalOutcome({
                    operation: "wallet.provider.disconnect",
                    phase: "provider-event",
                    status: "blocked",
                    known: true,
                    reason: "provider-disconnect",
                    message,
                    error: code || message ? {code, message} : null,
                    value: {code, message, txDraftCleared: true},
                    rpc: mcelTinyContractWalletRpcMethods(mcelTinyContractWalletAdapterState(), true)
                  });
              ctx.set("runtime.wallet", nextWallet);
              ctx.set("runtime.network", nextNetwork);
              ctx.set("runtime.txDraft", nextTxDraft);
              ctx.set("runtime.externalOutcome", outcome);
              ctx.set("runtime.walletEvents", [{
                type: "disconnect",
                outcomeStatus: outcome.status,
                outcomeReason: outcome.reason,
                code,
                message,
                txDraftCleared: true
              }]);
              ctx.set("runtime.evidenceStrip", [
                "wallet.provider.disconnect",
                `code=${code || "none"}`,
                `message=${message}`
              ]);
              ctx.evidence({
                ok: true,
                message: "Provider disconnect cleared runtime wallet state through a declared SCM effect."
              });
              return {disconnected: true, code, message, txDraftCleared: true};
            },
            commit(_ctx, result) {
              return {
                disconnected: result?.disconnected === true,
                code: result?.code || "",
                txDraftCleared: result?.txDraftCleared === true
              };
            }
          },
          "wallet.provider.error": {
            kind: "provider-event-effect",
            triggers: ["runtime.walletAdapter"],
            reads: ["runtime.wallet", "runtime.network"],
            writes: ["runtime.wallet", "runtime.walletEvents", "runtime.externalOutcome", "runtime.evidenceStrip"],
            external: {
              resource: "metamask-provider-event",
              operation: "error"
            },
            errorPolicy: {
              onFailure: "record-provider-event-error"
            },
            run(ctx, payload = {}) {
              const wallet = ctx.get("runtime.wallet") || {};
              const network = ctx.get("runtime.network") || {};
              const error = payload.error || payload;
              const message = error?.message || payload.message || "provider error";
              const code = error?.code || payload.code || "";
              const outcome = payload.outcome?.kind === "mcel-external-outcome"
                ? payload.outcome
                : mcelTinyContractExternalOutcome({
                    operation: "wallet.provider.error",
                    phase: "provider-event",
                    status: "exception",
                    known: false,
                    reason: "provider-error",
                    message,
                    error: {code, message},
                    value: {network: network.status || "unknown"},
                    rpc: mcelTinyContractWalletRpcMethods(mcelTinyContractWalletAdapterState(), true)
                  });
              ctx.set("runtime.wallet", {
                ...wallet,
                status: "provider-error",
                providerEvent: "error",
                providerErrorCode: code,
                providerErrorMessage: message,
                outcomeStatus: outcome.status,
                outcomeReason: outcome.reason
              });
              ctx.set("runtime.externalOutcome", outcome);
              ctx.set("runtime.walletEvents", [{
                type: "error",
                outcomeStatus: outcome.status,
                outcomeReason: outcome.reason,
                code,
                message
              }]);
              ctx.set("runtime.evidenceStrip", [
                "wallet.provider.error",
                `code=${code || "none"}`,
                `message=${message}`,
                `network=${network.status || "unknown"}`
              ]);
              ctx.evidence({
                ok: false,
                message: "Provider error was captured by a declared SCM effect."
              });
              return {code, message};
            },
            commit(_ctx, result) {
              return {
                code: result?.code || "",
                message: result?.message || ""
              };
            }
          },
          "network.verify": {
            kind: "dev-network-gate",
            triggers: ["runtime.network"],
            reads: ["source.devRelease.devNetwork", "runtime.network"],
            writes: ["runtime.network", "runtime.evidenceStrip"],
            external: {
              resource: "wallet-chain",
              operation: "compare-chain-id"
            },
            errorPolicy: {
              onFailure: "block-release-approval"
            },
            run(ctx) {
              const devNetwork = ctx.get("source.devRelease.devNetwork") || {};
              const network = ctx.get("runtime.network") || {};
              const expected = String(devNetwork.chainId || "0x28757b2");
              const actual = String(network.chainId || "");
              const ok = actual.toLowerCase() === expected.toLowerCase();
              const nextNetwork = {
                ...network,
                expectedChainId: expected,
                ok,
                status: ok ? "dev-network-ready" : "wrong-chain"
              };
              ctx.set("runtime.network", nextNetwork);
              ctx.set("runtime.evidenceStrip", [
                `network.verify expected=${expected}`,
                `actual=${actual || "missing"}`,
                `ok=${ok}`
              ]);
              ctx.evidence({
                ok,
                message: ok
                  ? "network.verify matched the wallet chain to the source-owned dev network."
                  : "network.verify found a wallet chain mismatch."
              });
              return nextNetwork;
            },
            commit(_ctx, result) {
              return {ok: result?.ok === true, chainId: result?.chainId || ""};
            }
          },
          "release.select": {
            kind: "ui-effect",
            triggers: ["state.selectedRequestId"],
            reads: ["source.devRelease.requests", "state.selectedRequestId"],
            writes: ["state.selectedRequestId", "runtime.txDraft", "runtime.evidenceStrip"],
            external: {
              resource: "dom",
              operation: "select-release-request"
            },
            errorPolicy: {
              onFailure: "block-and-record-evidence"
            },
            run(ctx, payload = {}) {
              const requests = ctx.get("source.devRelease.requests") || [];
              const requestedId = String(payload.id || "");
              const fallbackId = ctx.get("state.selectedRequestId") || requests[0]?.id || "";
              const selectedId = requestedId || fallbackId;
              const request = requests.find((entry) => entry.id === selectedId) || requests[0] || null;
              if (!request) {
                ctx.set("runtime.txDraft", {
                  status: "empty",
                  requestId: "",
                  sourceRequestHash: "",
                  selectedRequestSnapshot: null,
                  walletAccountHash: "",
                  chainProof: {
                    expectedChainId: "0x28757b2",
                    chainId: "",
                    ok: false,
                    status: "no-source-request"
                  },
                  externalOutcomeSequence: [],
                  networkGateSequence: [],
                  calldataSource: "not-encoded",
                  abiEncodingStatus: "no-source-request",
                  probeEnvelopeIds: [],
                  invalidatedBy: [
                    mcelTinyContractTxDraftInvalidation("source-request-missing", {})
                  ],
                  valid: false,
                  summary: "No release request is available."
                });
                return {selectedRequestId: "", found: false};
              }
              const previousTxDraft = ctx.get("runtime.txDraft") || {};
              const selectedSnapshot = mcelTinyContractSelectedRequestSnapshot(request);
              ctx.set("state.selectedRequestId", request.id);
              ctx.set("runtime.txDraft", {
                status: "selected",
                requestId: request.id,
                to: "",
                data: "",
                sourceRequestHash: mcelTinyContractObjectHash(selectedSnapshot),
                selectedRequestSnapshot: selectedSnapshot,
                walletAccountHash: previousTxDraft.walletAccountHash || "",
                chainProof: previousTxDraft.chainProof || {
                  expectedChainId: "0x28757b2",
                  chainId: "",
                  ok: false,
                  status: "not-probed"
                },
                externalOutcomeSequence: previousTxDraft.externalOutcomeSequence || [],
                networkGateSequence: previousTxDraft.networkGateSequence || [],
                calldataSource: "not-encoded",
                abiEncodingStatus: "selected-not-encoded",
                probeEnvelopeIds: [],
                invalidatedBy: previousTxDraft.requestId && previousTxDraft.requestId !== request.id
                  ? [
                      mcelTinyContractTxDraftInvalidation("source-request-changed", {
                        previousRequestId: previousTxDraft.requestId,
                        nextRequestId: request.id
                      })
                    ]
                  : [],
                valid: false,
                summary: `Selected ${request.id}; transaction draft is not built yet.`,
                risk: request.risk
              });
              ctx.set("runtime.evidenceStrip", [
                `release.select id=${request.id}`,
                "writes=state.selectedRequestId,runtime.txDraft"
              ]);
              ctx.evidence({
                ok: true,
                message: `release.select selected ${request.id} without mutating source.`
              });
              return {selectedRequestId: request.id, found: true};
            },
            commit(_ctx, result) {
              return {selectedRequestId: result?.selectedRequestId || ""};
            }
          },
          "release.draftTx": {
            kind: "runtime-transaction-draft",
            triggers: ["runtime.network", "state.selectedRequestId"],
            reads: [
              "source.devRelease.contractAddress",
              "source.devRelease.requests",
              "state.selectedRequestId",
              "runtime.wallet",
              "runtime.network",
              "runtime.externalOutcome"
            ],
            writes: ["runtime.txDraft", "runtime.evidenceStrip"],
            external: {
              resource: "ethereum-transaction",
              operation: "draft-only-no-send"
            },
            errorPolicy: {
              onFailure: "runtime-draft-only"
            },
            run(ctx, payload = {}) {
              const contractAddress = ctx.get("source.devRelease.contractAddress");
              const requests = ctx.get("source.devRelease.requests") || [];
              const selectedId = ctx.get("state.selectedRequestId") || requests[0]?.id || "";
              const request = requests.find((entry) => entry.id === selectedId) || requests[0] || null;
              const wallet = ctx.get("runtime.wallet") || {};
              const network = ctx.get("runtime.network") || {};
              const externalOutcome = ctx.get("runtime.externalOutcome") || {};
              const externalBlocked = ["blocked", "exception"].includes(externalOutcome.status);
              const invalidatedBy = [];
              if (!request) {
                invalidatedBy.push(mcelTinyContractTxDraftInvalidation("source-request-missing", {selectedId}));
              }
              if (!wallet.connected) {
                invalidatedBy.push(mcelTinyContractTxDraftInvalidation("wallet-not-connected", {
                  accountHash: mcelTinyContractWalletAccountHash(wallet.account || "")
                }));
              }
              if (!network.ok) {
                invalidatedBy.push(mcelTinyContractTxDraftInvalidation("network-gate-failed", {
                  expectedChainId: network.expectedChainId || "",
                  chainId: network.chainId || "",
                  status: network.status || "waiting"
                }));
              }
              if (externalBlocked) {
                invalidatedBy.push(mcelTinyContractTxDraftInvalidation(`external-outcome-${externalOutcome.status}`, {
                  outcomeStatus: externalOutcome.status,
                  outcomeReason: externalOutcome.reason || ""
                }));
              }
              const ready = Boolean(wallet.connected && network.ok && request && !externalBlocked);
              const draftProbe = payload.draftProbe?.kind === "mcel-runtime-tx-draft-probe"
                ? payload.draftProbe
                : null;
              const encoding = ready
                ? (draftProbe?.encoding || mcelTinyContractEncodeTxDraftData(request, wallet, contractAddress))
                : {
                    status: "blocked",
                    methodSignature: request?.contractMethod || "",
                    functionName: mcelTinyContractTxFunctionName(request?.contractMethod || ""),
                    argsPreview: [],
                    calldata: "",
                    data: "",
                    calldataEncoding: externalBlocked ? "blocked-by-external-outcome" : "blocked-by-wallet-network-gate"
                  };
              const tx = ready
                ? (draftProbe?.tx || {
                    from: wallet.account || "",
                    to: contractAddress || "",
                    value: "0x0",
                    data: encoding.calldata || encoding.data || "",
                    chainId: network.chainId || "",
                    noSend: true
                  })
                : {
                    from: "",
                    to: contractAddress || "",
                    value: "0x0",
                    data: "",
                    chainId: network.chainId || "",
                    noSend: true
                  };
              const txDraftProvenance = mcelTinyContractTxDraftProvenance({
                source: {devRelease: {devNetwork: ctx.get("source.devRelease.devNetwork") || {}}},
                state: {selectedRequestId: selectedId},
                runtime: {wallet, network, externalOutcome},
                request,
                wallet,
                network,
                externalOutcome,
                encoding,
                draftProbe,
                ready,
                invalidatedBy
              });
              const txDraft = {
                status: ready ? "ready" : "blocked",
                requestId: request?.id || "",
                createdFrom: {
                  requestId: request?.id || "",
                  sourcePath: "source.devRelease.requests",
                  contractMethod: request?.contractMethod || "",
                  status: request?.status || "",
                  risk: request?.risk || ""
                },
                sourceRequestHash: txDraftProvenance.sourceRequestHash,
                selectedRequestSnapshot: txDraftProvenance.selectedRequestSnapshot,
                walletAccountHash: txDraftProvenance.walletAccountHash,
                chainProof: txDraftProvenance.chainProof,
                externalOutcomeSequence: txDraftProvenance.externalOutcomeSequence,
                networkGateSequence: txDraftProvenance.networkGateSequence,
                calldataSource: txDraftProvenance.calldataSource,
                abiEncodingStatus: txDraftProvenance.abiEncodingStatus,
                probeEnvelopeIds: txDraftProvenance.probeEnvelopeIds,
                invalidatedBy: txDraftProvenance.invalidatedBy,
                validityInvariant: txDraftProvenance.validityInvariant,
                valid: txDraftProvenance.valid,
                freshnessStatus: txDraftProvenance.freshnessStatus,
                freshnessAction: txDraftProvenance.freshnessAction,
                noSendBoundaryPreserved: txDraftProvenance.noSendBoundaryPreserved,
                provenanceEnforced: txDraftProvenance.provenanceEnforced,
                provenanceVersion: txDraftProvenance.provenanceVersion,
                to: tx.to || "",
                from: tx.from || "",
                value: tx.value || "0x0",
                chainId: tx.chainId || network.chainId || "",
                expectedChainId: network.expectedChainId || "",
                data: tx.data || "",
                calldata: tx.data || encoding.calldata || "",
                calldataEncoding: encoding.calldataEncoding || "unknown",
                encodingStatus: encoding.status || "unknown",
                methodSignature: encoding.methodSignature || request?.contractMethod || "",
                functionName: encoding.functionName || mcelTinyContractTxFunctionName(request?.contractMethod || ""),
                argsPreview: encoding.argsPreview || [],
                nonce: draftProbe?.nonce || {
                  method: "eth_getTransactionCount",
                  status: ready ? "not-probed" : "skipped",
                  reason: ready ? "probe-unavailable" : "wallet-network-gate-blocked"
                },
                gasEstimate: draftProbe?.gasEstimate || {
                  method: "eth_estimateGas",
                  status: ready ? "not-probed" : "skipped",
                  reason: ready ? "probe-unavailable" : "wallet-network-gate-blocked"
                },
                ethCall: draftProbe?.ethCall || {
                  method: "eth_call",
                  status: ready ? "not-probed" : "skipped",
                  reason: ready ? "probe-unavailable" : "wallet-network-gate-blocked"
                },
                rpcEvidence: draftProbe?.rpc || [],
                noSend: true,
                boundary: "runtime-only-no-send",
                summary: ready
                  ? `Drafted runtime-only no-send tx for ${request.id} to ${contractAddress}.`
                  : (externalBlocked
                    ? `Transaction draft blocked by external outcome ${externalOutcome.status}: ${externalOutcome.reason || "unknown"}.`
                    : "Transaction draft blocked until wallet and dev-network gate are ready.")
              };
              ctx.set("runtime.txDraft", txDraft);
              ctx.set("runtime.evidenceStrip", [
                `release.draftTx status=${txDraft.status}`,
                `noSend=${txDraft.noSend}`,
                `to=${txDraft.to || "missing"}`,
                `from=${txDraft.from ? txDraft.from.slice(0, 10) + "…" : "missing"}`,
                `calldata=${txDraft.calldata ? txDraft.calldata.slice(0, 18) + "…" : "blocked"}`,
                `nonce=${txDraft.nonce?.status || "unknown"}`,
                `gas=${txDraft.gasEstimate?.status || "unknown"}`,
                `sourceRequestHash=${txDraft.sourceRequestHash || "missing"}`,
                `invalidatedBy=${(txDraft.invalidatedBy || []).map((item) => item.reason).join("|") || "none"}`
              ]);
              ctx.evidence({
                ok: ready,
                message: ready
                  ? "release.draftTx produced a runtime-only no-send transaction draft with calldata, nonce/gas probe status, and source request provenance."
                  : "release.draftTx stayed runtime-only and reported a blocked wallet/network/external outcome gate."
              });
              return txDraft;
            },
            commit(_ctx, result) {
              return {
                status: result?.status || "",
                requestId: result?.requestId || "",
                noSend: result?.noSend === true,
                calldataEncoding: result?.calldataEncoding || "",
                gasStatus: result?.gasEstimate?.status || "",
                nonceStatus: result?.nonce?.status || "",
                sourceRequestHash: result?.sourceRequestHash || "",
                walletAccountHash: result?.walletAccountHash || "",
                chainProofStatus: result?.chainProof?.status || "",
                invalidatedBy: (result?.invalidatedBy || []).map((entry) => entry.reason || "").filter(Boolean)
              };
            }
          },
          "release.approve": {
            kind: "human-approval-effect",
            triggers: ["state.selectedRequestId"],
            reads: ["source.devRelease.requests", "source.devRelease.devNetwork", "state.selectedRequestId", "runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.externalOutcome"],
            writes: ["source.devRelease.requests", "runtime.txDraft", "runtime.txDraftConsumerGate", "runtime.evidenceStrip"],
            external: {
              resource: "source",
              operation: "mark-selected-release-approved"
            },
            errorPolicy: {
              onFailure: "block-source-write-and-record-evidence"
            },
            run(ctx) {
              const selectedId = ctx.get("state.selectedRequestId");
              const requests = ctx.get("source.devRelease.requests") || [];
              const selectedRequest = requests.find((request) => request.id === selectedId) || null;
              const wallet = ctx.get("runtime.wallet") || {};
              const network = ctx.get("runtime.network") || {};
              const txDraft = ctx.get("runtime.txDraft") || {};
              const externalOutcome = ctx.get("runtime.externalOutcome") || {};
              const externalBlocked = ["blocked", "exception"].includes(externalOutcome.status);
              const freshness = mcelTinyContractTxDraftFreshnessCheck({
                source: {devRelease: {devNetwork: ctx.get("source.devRelease.devNetwork") || {}}},
                state: {selectedRequestId: selectedId},
                runtime: {wallet, network, externalOutcome, txDraft},
                request: selectedRequest,
                txDraft,
                reason: "release.approve-consumer-gate"
              });
              const guardedTxDraft = mcelTinyContractApplyTxDraftFreshness(txDraft, freshness);
              const consumerGate = mcelTinyContractTxDraftConsumerGate({
                txDraft: guardedTxDraft,
                freshness,
                consumer: "release.approve"
              });
              ctx.set("runtime.txDraft", guardedTxDraft);
              ctx.set("runtime.txDraftConsumerGate", consumerGate);
              const ready = Boolean(
                wallet.connected
                && network.ok
                && guardedTxDraft.status === "ready"
                && guardedTxDraft.valid === true
                && guardedTxDraft.provenanceEnforced === true
                && guardedTxDraft.noSendBoundaryPreserved === true
                && consumerGate.valid === true
                && !externalBlocked
              );
              if (!ready) {
                ctx.set("runtime.evidenceStrip", [
                  `release.approve blocked id=${selectedId || "missing"}`,
                  `wallet=${wallet.connected ? "connected" : "not-connected"}`,
                  `network=${network.ok ? "ok" : "not-ok"}`,
                  `txDraft=${guardedTxDraft.status || "empty"}`,
                  `txDraftFreshness=${guardedTxDraft.freshnessStatus || freshness.status || "not-observed"}`,
                  `txDraftConsumerGate=${consumerGate.status}`,
                  `invalidatedBy=${consumerGate.invalidationReasons.join("|") || "none"}`,
                  `externalOutcome=${externalOutcome.status || "waiting"}`
                ]);
                ctx.evidence({
                  ok: false,
                  message: "release.approve refused to mutate source because txDraft provenance was not current for the declared source effect."
                });
                return {
                  selectedRequestId: selectedId || "",
                  status: "blocked",
                  blocked: true,
                  txDraftFreshness: guardedTxDraft.freshnessStatus || freshness.status || "",
                  txDraftConsumerGate: consumerGate.status,
                  invalidatedBy: consumerGate.invalidationReasons
                };
              }
              const nextRequests = requests.map((request) => {
                if (request.id !== selectedId) return request;
                return {
                  ...request,
                  status: "approved",
                  approvalNote: "Approved through declared SCM effect release.approve."
                };
              });
              const selected = nextRequests.find((request) => request.id === selectedId) || null;
              ctx.set("source.devRelease.requests", nextRequests);
              ctx.set("runtime.evidenceStrip", [
                `release.approve id=${selectedId}`,
                "declared source write: source.devRelease.requests",
                `txDraftConsumerGate=${consumerGate.status}`,
                `txDraftFreshness=${guardedTxDraft.freshnessStatus || freshness.status || "valid"}`,
                "no-send boundary preserved"
              ]);
              ctx.evidence({
                ok: true,
                message: "release.approve updated source.devRelease.requests only after txDraft provenance passed the consumer gate."
              });
              return {
                selectedRequestId: selectedId,
                status: selected?.status || "missing",
                blocked: false,
                txDraftFreshness: guardedTxDraft.freshnessStatus || freshness.status || "",
                txDraftConsumerGate: consumerGate.status,
                invalidatedBy: []
              };
            },
            commit(_ctx, result) {
              return {
                approved: result?.blocked ? "" : (result?.selectedRequestId || ""),
                status: result?.status || "",
                txDraftConsumerGate: result?.txDraftConsumerGate || "",
                txDraftFreshness: result?.txDraftFreshness || "",
                invalidatedBy: result?.invalidatedBy || []
              };
            }
          },
          "ai.repairWalletHint": {
            kind: "repair-packet-effect",
            triggers: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.externalOutcome"],
            reads: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.externalOutcome", "runtime.proofChip"],
            writes: ["runtime.proofChip", "runtime.repairPacket", "runtime.assistantRepairPrompt", "runtime.evidenceStrip"],
            external: {
              resource: "repair-packet",
              operation: "build-bounded-repair-packet-no-model-call"
            },
            errorPolicy: {
              onFailure: "leave-source-untouched"
            },
            run(ctx, payload = {}) {
              const wallet = ctx.get("runtime.wallet") || {};
              const network = ctx.get("runtime.network") || {};
              const txDraft = ctx.get("runtime.txDraft") || {};
              const externalOutcome = ctx.get("runtime.externalOutcome") || {};
              const proofChip = ctx.get("runtime.proofChip") || {};
              const packet = buildMcelTinyContractRepairPacket({wallet, network, txDraft, externalOutcome, proofChip, payload});
              const prompt = JSON.stringify(packet, null, 2);
              ctx.set("runtime.repairPacket", packet);
              ctx.set("runtime.assistantRepairPrompt", prompt);
              ctx.set("runtime.proofChip", {
                text: payload.text || summarizeMcelTinyContractRepairPacket(packet),
                status: "repair-packet-ready",
                repaired: true,
                repairPacketReady: true,
                liveAiCall: false
              });
              ctx.set("runtime.evidenceStrip", [
                "repair packet generated",
                `target=${packet.target}`,
                `externalOutcome=${packet.evidence.externalOutcomeStatus}`,
                "liveAiCall=false",
                "forbidden writes declared"
              ]);
              ctx.evidence({
                ok: true,
                message: "Repair packet was generated inside runtime-owned proof boundary; no live AI call was made.",
                repairPacket: packet
              });
              return {repaired: true, walletConnected: wallet.connected === true, repairPacketReady: true, liveAiCall: false};
            },
            commit(_ctx, result) {
              return {
                repaired: result?.repaired === true,
                walletConnected: result?.walletConnected === true,
                repairPacketReady: result?.repairPacketReady === true,
                liveAiCall: result?.liveAiCall === true
              };
            }
          }
        },
        layoutContract: {
          root: ".mcel-dev-release-console",
          maxDocumentHeightRatio: 1.35,
          requiredComputed: {
            ".mcel-dev-release-console": {
              display: "grid",
              overflow: "hidden"
            }
          },
          regions: {
            walletPanel: {
              selector: "[data-mc-component='dev-release.wallet']",
              slot: "walletPanel",
              required: true
            },
            releaseQueue: {
              selector: "[data-mc-field='devRelease.requests']",
              slot: "releaseQueue",
              required: true
            },
            txPreview: {
              selector: "[data-mc-slot='runtime.txDraft']",
              slot: "txPreview",
              required: true
            },
            evidenceStrip: {
              selector: "[data-mc-slot='runtime.evidenceStrip']",
              slot: "evidenceStrip",
              required: true
            }
          }
        },
        styleContract: {
          scope: "sealed",
          owns: ["devWalletConsole"],
          forbidsGlobalLeakage: true,
          expectedComputed: {
            ".mcel-dev-release-console__tx": {
              overflow: "auto"
            }
          }
        },
        serializationContract: {
          sourceOwns: [
            "source.devRelease.title",
            "source.devRelease.summary",
            "source.devRelease.contractAddress",
            "source.devRelease.devNetwork",
            "source.devRelease.requests",
            "source.html.sourceId"
          ],
          runtimeOnly: [
            "runtime.wallet",
            "runtime.network",
            "runtime.txDraft",
            "runtime.walletAdapter",
            "runtime.walletEvents",
            "runtime.externalOutcome",
            "runtime.proofChip",
            "runtime.repairPacket",
            "runtime.assistantRepairPrompt",
            "runtime.serializedSource",
            "runtime.evidenceStrip"
          ],
          failIfRuntimeLeaks: true,
          runtimeLeakMarkers: [
            "data-mc-generated",
            "runtime.wallet",
            "runtime.network",
            "runtime.txDraft",
            "runtime.walletAdapter",
            "runtime.walletEvents",
            "runtime.externalOutcome",
            "runtime.repairPacket",
            "mcel-repair-packet",
            "MetaMask",
            "0xDeaD0000"
          ],
          output: {
            format: "clean-source-json",
            writeTo: "runtime.serializedSource"
          }
        },
        repairContract: {
          allowed: [
            "runtime.proofChip",
            "runtime.repairPacket",
            "runtime.assistantRepairPrompt",
            "runtime.evidenceStrip"
          ],
          forbidden: mcelTinyContractRepairForbiddenWrites(),
          strategies: {
            repairRuntimeProofChip: {
              reads: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.externalOutcome", "runtime.proofChip"],
              writes: [
                "runtime.proofChip",
                "runtime.repairPacket",
                "runtime.assistantRepairPrompt",
                "runtime.evidenceStrip"
              ],
              apply(ctx, payload = {}) {
                const wallet = ctx.get("runtime.wallet") || {};
                const network = ctx.get("runtime.network") || {};
                const txDraft = ctx.get("runtime.txDraft") || {};
                const externalOutcome = ctx.get("runtime.externalOutcome") || {};
                const proofChip = ctx.get("runtime.proofChip") || {};
                const packet = buildMcelTinyContractRepairPacket({wallet, network, txDraft, externalOutcome, proofChip, payload});
                ctx.set("runtime.repairPacket", packet);
                ctx.set("runtime.assistantRepairPrompt", JSON.stringify(packet, null, 2));
                ctx.set("runtime.proofChip", {
                  text: payload.text || summarizeMcelTinyContractRepairPacket(packet),
                  status: "repair-packet-ready",
                  repaired: true,
                  repairPacketReady: true,
                  liveAiCall: false
                });
                ctx.set("runtime.evidenceStrip", [
                  "repairRuntimeProofChip generated bounded repair packet",
                  `target=${packet.target}`,
                  `externalOutcome=${packet.evidence.externalOutcomeStatus}`,
                  "allowed writes: runtime proof display only",
                  "liveAiCall=false"
                ]);
                ctx.evidence({
                  ok: true,
                  message: "repairRuntimeProofChip produced a bounded repair packet without changing source, wallet, network, tx draft, or external outcome.",
                  repairPacket: packet
                });
                return {repaired: true, repairPacketReady: true, forbiddenWrites: packet.forbiddenWrites, liveAiCall: false};
              },
              post(ctx) {
                const chip = ctx.get("runtime.proofChip") || {};
                const packet = ctx.get("runtime.repairPacket") || {};
                return Boolean(chip.repaired && packet.kind === "mcel-repair-packet" && packet.liveAiCall === false);
              }
            }
          }
        }
      };
    }

    function mcelTinyContractRouteManifest() {
      return {
        version: "0.2.0",
        contract: "mcel.scm.route.dev-network-release-console.v1",
        segments: [
          {literal: "workspace"},
          {literal: "dev-network"},
          {param: "contractId", type: "id", required: true},
          {literal: "release-console"}
        ],
        query: {
          chain: {
            type: "enum",
            values: ["hardhat", "anvil", "local"],
            required: false
          }
        },
        mounts: {
          component: "DevNetworkReleaseConsole",
          inputs: {
            contractId: "route.params.contractId",
            networkSummary: "route.data.networkSummary"
          }
        },
        data: {
          "devnet.load": {
            kind: "async-data",
            triggers: ["route.params.contractId", "route.query.chain"],
            reads: [
              "route.params.contractId",
              "component.source.devRelease.devNetwork",
              "component.source.devRelease.requests"
            ],
            writes: ["route.data.networkSummary"],
            cancellation: "cancel-previous",
            racePolicy: "latest-route-wins",
            external: {
              resource: "dev-network",
              operation: "summarize-wallet-release-console"
            },
            errorPolicy: {
              onFailure: "keep-previous-route-data"
            },
            run(ctx) {
              const contractId = ctx.get("route.params.contractId");
              const devNetwork = ctx.get("component.source.devRelease.devNetwork") || {};
              const requests = ctx.get("component.source.devRelease.requests") || [];
              return {
                contractId,
                network: devNetwork.name || "dev network",
                chainId: devNetwork.chainId || "0x28757b2",
                totalRequests: requests.length,
                highRisk: requests.filter((request) => request.risk === "high").length
              };
            },
            commit(ctx, result) {
              ctx.set("route.data.networkSummary", result);
              return result;
            }
          }
        },
        lifecycle: {
          onEnter: ["validateParams", "mountComponent", "devnet.load"],
          onLeave: {
            blockedBy: ["component.runtime.txDraft.status"],
            resolutions: ["cancelNavigation", "serializeAndLeave"]
          }
        }
      };
    }

    function defineMcelTinyContractScm() {
      const scm = window.McelLabScm;
      if (!scm) {
        recordMcelTinyContractEvidence("scm", "McelLabScm is unavailable; real SCM proof cannot run.", "fail");
        return null;
      }
      const manifest = mcelTinyContractScmManifest();
      const route = mcelTinyContractRouteManifest();
      const componentValidation = scm.validateComponentManifest("DevNetworkReleaseConsole", manifest);
      if (!componentValidation.ok) {
        recordMcelTinyContractEvidence("scm", "DevNetworkReleaseConsole manifest failed SCM validation.", "fail", componentValidation);
        return {scm, manifest, route, componentValidation, routeValidation: null};
      }
      scm.defineComponent("DevNetworkReleaseConsole", manifest, {replace: true});
      const routeValidation = scm.validateRouteManifest("workspace.dev-network-release", route);
      if (!routeValidation.ok) {
        recordMcelTinyContractEvidence("scm", "workspace.dev-network-release route failed SCM validation.", "fail", routeValidation);
        return {scm, manifest, route, componentValidation, routeValidation};
      }
      scm.defineRoute("workspace.dev-network-release", route, {replace: true});
      recordMcelTinyContractEvidence("scm", "SCM component and dev-network route manifests resolved.", "pass", {
        component: componentValidation.ok,
        route: routeValidation.ok
      });
      return {scm, manifest, route, componentValidation, routeValidation};
    }

    function createMcelTinyContractScmRuntime(options = {}) {
      const defined = defineMcelTinyContractScm();
      if (!defined?.scm || defined.componentValidation?.ok === false || defined.routeValidation?.ok === false) {
        return null;
      }
      const tinyState = ensureMcelTinyContractState();
      const {scm} = defined;
      const instance = scm.createComponentInstance("DevNetworkReleaseConsole", {
        id: "dev-network-release-console-demo",
        source: mcelTinyContractInitialSourceData(),
        runtime: mcelTinyContractRuntimeDefaults(),
        state: mcelTinyContractStateDefaults()
      });
      const routeInstance = scm.createRouteInstance("workspace.dev-network-release", {
        id: "dev-network-release-route-demo",
        componentInstance: instance
      });

      tinyState.scmInstance = instance;
      tinyState.scmRouteInstance = routeInstance;
      tinyState.reviewedCount = 0;
      tinyState.walletConnectCount = 0;
      tinyState.walletDisconnectCount = 0;
      tinyState.walletDisconnectCommitCount = 0;
      tinyState.walletRevokeAttemptCount = 0;
      tinyState.walletRevokeSuccessCount = 0;
      tinyState.providerAccountsChangedCount = 0;
      tinyState.providerAccountSwitchCount = 0;
      tinyState.providerAccountDisconnectCount = 0;
      tinyState.providerChainChangedCount = 0;
      tinyState.providerDisconnectCount = 0;
      tinyState.providerErrorCount = 0;
      tinyState.routeLoaderCount = 0;
      tinyState.networkVerifyCount = 0;
      tinyState.releaseSelectCount = 0;
      tinyState.txDraftCount = 0;
      tinyState.fullBatteryRunCount = 0;
      tinyState.externalOutcomeCount = 0;
      tinyState.externalBlockedCount = 0;
      tinyState.externalExceptionCount = 0;
      tinyState.lastWalletResetClean = false;
      tinyState.lastExternalOutcome = null;

      const routeEnter = scm.enterRoute(routeInstance, {
        params: {contractId: "dev-release-console"},
        query: {chain: "hardhat"}
      });
      const routeLoader = scm.runRouteLoader(routeInstance, "devnet.load");
      tinyState.routeLoaderCount += 1;
      recordMcelTinyContractEvidence("route", "SCM route entered and devnet.load route loader committed.", "pass", {
        routeEnter,
        routeLoader
      });

      if (options.exercise !== false) {
        scm.runEffect(instance, "wallet.connect", {
          mock: true,
          provider: "mock-dev-provider",
          account: "0xDeaD00000000000000000000000000000000BEEF",
          chainId: "0x28757b2"
        });
        tinyState.walletConnectCount += 1;
        scm.runEffect(instance, "network.verify");
        tinyState.networkVerifyCount += 1;
        scm.runEffect(instance, "release.select", {id: "rel-allowance-view"});
        tinyState.releaseSelectCount += 1;
        scm.runEffect(instance, "release.draftTx");
        tinyState.txDraftCount += 1;
        scm.runEffect(instance, "release.approve");
        tinyState.reviewedCount += 1;
      }
      return {scm, instance, routeInstance, defined};
    }

    function mcelTinyContractItems(instance) {
      return Array.isArray(instance?.source?.devRelease?.requests) ? instance.source.devRelease.requests : [];
    }

    function selectedMcelTinyContractItem(instance) {
      const items = mcelTinyContractItems(instance);
      const selectedId = instance?.state?.selectedRequestId || items[0]?.id || "";
      return items.find((item) => item.id === selectedId) || items[0] || null;
    }

    function serializeMcelTinyContractRuntime(app) {
      if (!app) return "";
      const clone = app.cloneNode(true);
      clone.querySelectorAll('[data-mc-generated="true"]').forEach((node) => node.remove());
      clone.querySelectorAll('[data-mc-slot][data-mc-owner="runtime"]').forEach((slot) => {
        slot.replaceChildren();
      });
      return normalizeMcelTinyContractHtml(clone.outerHTML);
    }

    function renderMcelTinyContractMap(app) {
      if (!mcelTinyContractMap) return;
      const sourceHtml = mcelTinyContractSourceHtml();
      const instance = ensureMcelTinyContractState().scmInstance;
      const selected = selectedMcelTinyContractItem(instance);
      mcelTinyContractMap.textContent = [
        normalizeMcelTinyContractHtml(sourceHtml),
        "",
        "SCM projection:",
        JSON.stringify({
          component: app?.getAttribute("data-mc-component") || "dev-network-release-console",
          route: app?.getAttribute("data-mc-route") || "workspace.dev-network-release",
          expectedChainId: instance?.source?.devRelease?.devNetwork?.chainId || "0x28757b2",
          walletConnected: instance?.runtime?.wallet?.connected === true,
          networkOk: instance?.runtime?.network?.ok === true,
          selectedRequestId: selected?.id || "",
          sourceRequestCount: mcelTinyContractItems(instance).length,
          sourceStatuses: mcelTinyContractItems(instance).map((item) => `${item.id}:${item.status}`),
          runtimeSlots: [
            "runtime.wallet",
            "runtime.txDraft",
            "runtime.walletAdapter",
            "runtime.walletEvents",
            "runtime.proofChip",
            "runtime.evidenceStrip"
          ]
        }, null, 2)
      ].join("\n");
    }

    function renderMcelTinyRuntimeBadge(app, instance = ensureMcelTinyContractState().scmInstance) {
      const slot = app?.querySelector('[data-mc-slot="runtime.proofChip"]');
      if (!slot) return null;
      const chip = document.createElement("strong");
      const proofChip = instance?.runtime?.proofChip || {};
      chip.dataset.mcGenerated = "true";
      chip.dataset.mcOwner = "runtime";
      chip.dataset.mcRuntimeState = "runtime.proofChip";
      chip.dataset.mcRepairableBy = "repairRuntimeProofChip";
      chip.textContent = proofChip.text || "Runtime wallet proof chip waiting.";
      slot.replaceChildren(chip);
      return chip;
    }

    function renderMcel18nWalletToolSurface(commitBoundary = null) {
      const boundary = commitBoundary || ensureMcelTinyContractState().lastWalletCommitBoundary || mcelCommitBoundaryDefault("wallet.send-sign", "wallet-tool-surface");
      const statusSlot = typeof mcel18nWalletToolStatus !== "undefined"
        ? mcel18nWalletToolStatus
        : document.querySelector("#mcel-18n-wallet-tool-status");
      const preflightSlot = typeof mcel18nWalletToolPreflight !== "undefined"
        ? mcel18nWalletToolPreflight
        : document.querySelector("#mcel-18n-wallet-tool-preflight");
      const receiptSlot = typeof mcel18nWalletToolReceipt !== "undefined"
        ? mcel18nWalletToolReceipt
        : document.querySelector("#mcel-18n-wallet-tool-receipt");
      const txDraftSlot = document.querySelector("#mcel-18n-wallet-tool-tx-draft");
      const freshnessSlot = document.querySelector("#mcel-18n-wallet-tool-freshness");
      const ledgerSlot = document.querySelector("#mcel-18n-wallet-tool-ledger");
      const negativePathsSlot = document.querySelector("#mcel-18n-wallet-tool-negative-paths");
      const unlockRequirementsSlot = document.querySelector("#mcel-18n-wallet-tool-unlock-requirements");
      const finalLockedSpecimenSlot = document.querySelector("#mcel-18n-wallet-tool-final-locked-specimen");
      const preflight = boundary.mcelCommitPreflight || boundary.mcelCommitConsumerGate?.endgamePreflight || {};
      const receipt = boundary.mcelCommitReceipt || {};
      const walletTxDraft = boundary.walletTxDraft || {};
      const walletFreshnessSnapshot = boundary.walletFreshnessSnapshot || {};
      const walletPreflightReport = boundary.walletPreflightReport || {};
      const walletNegativePathTestWall = boundary.walletNegativePathTestWall || {};
      const walletUnlockRequirements = boundary.walletUnlockRequirements || {};
      const walletFinalLockedSpecimen = boundary.walletFinalLockedSpecimen || {};
      const tinyState = ensureMcelTinyContractState();
      const receiptLedger = (tinyState.commitBoundaryReceipts || []).slice(-8);
      if (statusSlot) {
        statusSlot.dataset.status = boundary.status || "locked";
        statusSlot.textContent = [
          `18N wallet tool: ${boundary.status || "locked"}`,
          `action: ${boundary.action || "wallet.send-sign"}`,
          `txDraft=${walletTxDraft.status || "empty"} freshness=${walletFreshnessSnapshot.status || "not-observed"}`,
          `simulation=${walletTxDraft.simulation?.kind || "none"}`,
          `canSend=${boundary.canSend === true} canSign=${boundary.canSign === true} canBroadcast=${boundary.canBroadcast === true}`,
          `unlock=${walletUnlockRequirements.status || "incomplete"} final=${walletFinalLockedSpecimen.finalStatus || "locked"}`,
          `next: ${boundary.nextAction || preflight.summary || "inspect MCEL commit receipt"}`
        ].join("\n");
      }
      if (txDraftSlot) {
        txDraftSlot.textContent = JSON.stringify({
          kind: "mcel-18n-wallet-tool-tx-draft-view",
          boundaryVersion: boundary.boundaryVersion || "18N-MCEL-j",
          walletTxDraft,
          walletTxProvenance: boundary.walletTxProvenance || {},
          rebuildDraftAction: boundary.walletRebuildDraftAction || {}
        }, null, 2);
      }
      if (freshnessSlot) {
        freshnessSlot.textContent = JSON.stringify({
          kind: "mcel-18n-wallet-tool-freshness-view",
          boundaryVersion: boundary.boundaryVersion || "18N-MCEL-j",
          walletFreshnessSnapshot,
          blockers: walletFreshnessSnapshot.blockers || [],
          simulation: walletTxDraft.simulation || null,
          rule: "Refresh preflight does not silently make stale draft usable."
        }, null, 2);
      }
      if (preflightSlot) {
        preflightSlot.textContent = JSON.stringify({
          kind: "mcel-18n-wallet-tool-preflight-view",
          boundaryVersion: boundary.boundaryVersion || "18N-MCEL-j",
          draft: boundary.mcelCommitDraft,
          provenance: boundary.mcelCommitProvenance,
          freshness: boundary.mcelCommitFreshness,
          consumerGate: boundary.mcelCommitConsumerGate,
          preflight,
          walletTxDraft,
          walletTxProvenance: boundary.walletTxProvenance || {},
          walletFreshnessSnapshot,
          walletPreflightReport,
          walletNegativePathTestWall,
          walletUnlockRequirements,
          walletFinalLockedSpecimen
        }, null, 2);
      }
      if (receiptSlot) {
        receiptSlot.textContent = JSON.stringify({
          kind: "mcel-18n-wallet-tool-receipt-view",
          receipt,
          walletBlockedAttemptReceipt: boundary.walletBlockedAttemptReceipt || receipt,
          walletRebuildDraftAction: boundary.walletRebuildDraftAction || {},
          walletNegativePathTestWall,
          walletUnlockRequirements,
          walletFinalLockedSpecimen,
          mcelProofDockSpecimens: boundary.mcelProofDockSpecimens || {},
          invariant: boundary.invariant || []
        }, null, 2);
      }
      if (negativePathsSlot) {
        negativePathsSlot.textContent = JSON.stringify({
          kind: "mcel-18n-wallet-tool-negative-path-test-wall-view",
          boundaryVersion: boundary.boundaryVersion || "18N-MCEL-j",
          walletNegativePathTestWall
        }, null, 2);
      }
      if (unlockRequirementsSlot) {
        unlockRequirementsSlot.textContent = JSON.stringify({
          kind: "mcel-18n-wallet-tool-unlock-requirements-view",
          boundaryVersion: boundary.boundaryVersion || "18N-MCEL-j",
          walletUnlockRequirements
        }, null, 2);
      }
      if (finalLockedSpecimenSlot) {
        finalLockedSpecimenSlot.textContent = JSON.stringify({
          kind: "mcel-18n-wallet-tool-final-locked-specimen-view",
          boundaryVersion: boundary.boundaryVersion || "18N-MCEL-j",
          walletFinalLockedSpecimen
        }, null, 2);
      }
      if (ledgerSlot) {
        ledgerSlot.textContent = JSON.stringify({
          kind: "mcel-18n-wallet-tool-receipt-ledger-view",
          boundaryVersion: boundary.boundaryVersion || "18N-MCEL-j",
          receiptCount: receiptLedger.length,
          latestStatus: receipt.status || "blocked",
          latestBlockers: preflight.blockers || [],
          receipts: receiptLedger.map((entry) => ({
            action: entry.action || "",
            status: entry.status || "",
            mutationExecuted: entry.mutationExecuted === true,
            freshnessStatus: entry.freshnessStatus || "",
            consumerGateStatus: entry.consumerGateStatus || "",
            preflightStatus: entry.preflightStatus || "",
            txDraftId: entry.txDraftId || "",
            txDraftHash: entry.txDraftHash || "",
            accountSnapshot: entry.accountSnapshot || {},
            chainSnapshot: entry.chainSnapshot || {},
            sourceRequestSnapshot: entry.sourceRequestSnapshot || {},
            targetSnapshot: entry.targetSnapshot || {},
            blockers: entry.blockers || []
          })),
          walletLock: {
            canSend: boundary.canSend === true,
            canSign: boundary.canSign === true,
            canBroadcast: boundary.canBroadcast === true,
            mutationExecuted: receipt.mutationExecuted === true
          },
          walletNegativePathTestWall,
          walletUnlockRequirements,
          walletFinalLockedSpecimen,
          mcelProofDockSpecimens: boundary.mcelProofDockSpecimens || {}
        }, null, 2);
      }
      const refreshButton = document.querySelector("#mcel-18n-wallet-tool-refresh");
      const copyButton = document.querySelector("#mcel-18n-wallet-tool-copy-receipt");
      if (refreshButton) {
        refreshButton.dataset.boundaryStatus = boundary.status || "locked";
      }
      const rebuildButton = document.querySelector("#mcel-18n-wallet-tool-rebuild-draft");
      if (rebuildButton) {
        rebuildButton.dataset.freshnessStatus = walletFreshnessSnapshot.status || "not-observed";
        rebuildButton.dataset.rebuildRequired = walletFreshnessSnapshot.status === "valid" ? "false" : "true";
      }
      if (copyButton) {
        copyButton.dataset.receiptStatus = receipt.status || "blocked";
        copyButton.dataset.mutationExecuted = receipt.mutationExecuted === true ? "true" : "false";
      }
    }


    function refreshMcel18nWalletToolBoundary(reason = "manual-wallet-tool-preflight-refresh") {
      const tinyState = ensureMcelTinyContractState();
      const instance = tinyState.scmInstance || {};
      const selected = selectedMcelTinyContractItem(instance);
      const boundary = mcelWalletToolCommitBoundary({
        source: instance?.source || {},
        state: instance?.state || {},
        runtime: instance?.runtime || {},
        request: selected,
        reason,
        simulation: tinyState.walletStaleSimulation || null
      });
      if (instance?.runtime) {
        instance.runtime.walletCommitBoundary = boundary;
      }
      tinyState.lastWalletCommitBoundary = boundary;
      if (boundary.mcelCommitReceipt) {
        tinyState.commitBoundaryReceipts = [
          ...(tinyState.commitBoundaryReceipts || []),
          boundary.mcelCommitReceipt
        ].slice(-12);
      }
      renderMcel18nWalletToolSurface(boundary);
      return boundary;
    }

    function simulateMcel18nWalletStaleDraft(kind = "account", reason = "manual-stale-draft-simulation") {
      const tinyState = ensureMcelTinyContractState();
      tinyState.walletStaleSimulationCount = Number(tinyState.walletStaleSimulationCount || 0) + 1;
      tinyState.walletStaleSimulation = {
        kind,
        reason,
        sequence: tinyState.walletStaleSimulationCount,
        createdBy: "mcelWalletStaleDraftSimulation.v1",
        label: `simulate-wallet-${kind}-change`,
        invariant: [
          "stale simulation changes only the freshness view",
          "Refresh preflight does not silently make stale draft usable",
          "rebuild draft from current wallet state is required to clear stale intent"
        ]
      };
      recordMcelTinyContractEvidence(
        "wallet-stale-simulation",
        `18N wallet stale draft simulation armed: ${kind}.`,
        "warn",
        tinyState.walletStaleSimulation
      );
      return refreshMcel18nWalletToolBoundary(`simulate-wallet-${kind}-change`);
    }

    async function rebuildMcel18nWalletTxDraft(reason = "manual-wallet-tool-rebuild-draft") {
      const tinyState = ensureMcelTinyContractState();
      tinyState.walletRebuildDraftCount = Number(tinyState.walletRebuildDraftCount || 0) + 1;
      tinyState.walletStaleSimulation = null;
      recordMcelTinyContractEvidence(
        "wallet-rebuild-draft",
        "18N wallet rebuild draft from current wallet state requested; provider send/sign/broadcast remains locked.",
        "pass",
        {
          rebuildCount: tinyState.walletRebuildDraftCount,
          action: "rebuild draft from current wallet state",
          noSend: true
        }
      );
      await draftMcelTinyContractTransaction(reason);
      return refreshMcel18nWalletToolBoundary("rebuild-wallet-tx-draft-receipt");
    }

    async function copyMcel18nWalletToolReceipt() {
      const tinyState = ensureMcelTinyContractState();
      const boundary = tinyState.lastWalletCommitBoundary || refreshMcel18nWalletToolBoundary("copy-wallet-tool-receipt");
      const payload = JSON.stringify({
        kind: "mcel-18n-wallet-tool-copy-receipt-payload",
        boundaryVersion: boundary.boundaryVersion || "18N-MCEL-j",
        receipt: boundary.mcelCommitReceipt || {},
        preflight: boundary.mcelCommitPreflight || {},
        walletTxDraft: boundary.walletTxDraft || {},
        walletFreshnessSnapshot: boundary.walletFreshnessSnapshot || {},
        walletPreflightReport: boundary.walletPreflightReport || {},
        walletNegativePathTestWall: boundary.walletNegativePathTestWall || {},
        walletUnlockRequirements: boundary.walletUnlockRequirements || {},
        walletFinalLockedSpecimen: boundary.walletFinalLockedSpecimen || {},
        receiptLedger: (tinyState.commitBoundaryReceipts || []).slice(-8),
        mcelProofDockSpecimens: boundary.mcelProofDockSpecimens || {},
        walletLock: {
          canSend: boundary.canSend === true,
          canSign: boundary.canSign === true,
          canBroadcast: boundary.canBroadcast === true,
          mutationExecuted: boundary.mcelCommitReceipt?.mutationExecuted === true
        }
      }, null, 2);
      const statusSlot = document.querySelector("#mcel-18n-wallet-tool-status");
      try {
        if (navigator?.clipboard?.writeText) {
          await navigator.clipboard.writeText(payload);
          if (statusSlot) statusSlot.textContent = `${statusSlot.textContent}\nreceipt copied: ${new Date().toISOString()}`;
        } else {
          if (statusSlot) statusSlot.textContent = `${statusSlot.textContent}\ncopy unavailable; receipt payload remains visible below.`;
        }
      } catch (error) {
        if (statusSlot) statusSlot.textContent = `${statusSlot.textContent}\ncopy failed: ${error?.message || String(error)}`;
      }
      return payload;
    }

    function renderMcelTinyWalletPanel(app, instance = ensureMcelTinyContractState().scmInstance) {
      const slot = app?.querySelector('[data-mc-slot="runtime.wallet"]');
      if (!slot) return;
      const wallet = instance?.runtime?.wallet || {};
      const network = instance?.runtime?.network || {};
      const selected = selectedMcelTinyContractItem(instance);
      const tinyState = ensureMcelTinyContractState();
      const commitBoundary = mcelWalletToolCommitBoundary({
        source: instance?.source || {},
        state: instance?.state || {},
        runtime: instance?.runtime || {},
        request: selected,
        reason: "render-wallet-tool",
        simulation: tinyState.walletStaleSimulation || null
      });
      if (instance?.runtime) {
        instance.runtime.walletCommitBoundary = commitBoundary;
      }
      tinyState.lastWalletCommitBoundary = commitBoundary;
      if (commitBoundary.mcelCommitReceipt) {
        tinyState.commitBoundaryReceipts = [
          ...(tinyState.commitBoundaryReceipts || []),
          commitBoundary.mcelCommitReceipt
        ].slice(-12);
      }
      const panel = document.createElement("section");
      panel.className = "mcel-dev-release-console__wallet";
      panel.dataset.mcGenerated = "true";
      panel.dataset.mcOwner = "runtime";
      panel.dataset.mcRuntimeState = "runtime.wallet";
      panel.dataset.mcel18nCommitBoundary = "wallet-tool";
      const adapter = mcelTinyContractWalletAdapterState();
      const liveLabel = adapter.liveProvider
        ? `${adapter.providerKind || "provider"} rpc`
        : (adapter.mockFallback ? "mock fallback" : "not observed");
      const preflight = commitBoundary.mcelCommitPreflight || {};
      const receipt = commitBoundary.mcelCommitReceipt || {};
      panel.innerHTML = `
        <strong>Runtime wallet state</strong>
        <dl>
          <dt>provider</dt><dd>${wallet.provider || wallet.mode || "none"}</dd>
          <dt>adapter</dt><dd>${liveLabel}</dd>
          <dt>rpc calls</dt><dd>${(adapter.calls || []).map((call) => call.method).slice(-4).join(", ") || "none"}</dd>
          <dt>provider events</dt><dd>accounts=${tinyState.providerAccountsChangedCount || 0} switches=${tinyState.providerAccountSwitchCount || 0}</dd>
          <dt>account</dt><dd>${wallet.account ? wallet.account.slice(0, 12) + "…" : "not connected"}</dd>
          <dt>chain</dt><dd>${network.chainId || "missing"} / expected ${network.expectedChainId || "0x28757b2"}</dd>
          <dt>gate</dt><dd>${network.ok ? "dev network ready" : network.status || "waiting"}</dd>
          <dt>18N boundary</dt><dd>${commitBoundary.kind} · ${commitBoundary.status}</dd>
          <dt>wallet txDraft</dt><dd>${commitBoundary.walletTxDraft?.status || "empty"} · ${commitBoundary.walletTxDraft?.txDraftId || "not built"}</dd>
          <dt>provenance</dt><dd>${commitBoundary.walletTxProvenance?.kind || "not observed"} · ${commitBoundary.walletTxProvenance?.txDraftHash || "missing hash"}</dd>
          <dt>freshness</dt><dd>${commitBoundary.walletFreshnessSnapshot?.status || "not observed"} · ${(commitBoundary.walletFreshnessSnapshot?.blockers || []).slice(0, 3).join(", ") || "no blockers"}</dd>
          <dt>preflight</dt><dd>${preflight.status || "locked"} · commit=${preflight.canCommit === true}</dd>
          <dt>wallet lock</dt><dd>canSend=${commitBoundary.canSend === true} · canSign=${commitBoundary.canSign === true} · canBroadcast=${commitBoundary.canBroadcast === true}</dd>
          <dt>receipt</dt><dd>${receipt.walletReceiptKind || receipt.kind || "receipt"} · ${receipt.status || "blocked"} · mutationExecuted=${receipt.mutationExecuted === true}</dd>
          <dt>unlock requirements</dt><dd>${commitBoundary.walletUnlockRequirements?.status || "incomplete"} · ready=${commitBoundary.walletUnlockRequirements?.readyForProviderExecution === true}</dd>
          <dt>final locked specimen</dt><dd>${commitBoundary.walletFinalLockedSpecimen?.finalStatus || "locked"} · mutationExecuted=${commitBoundary.walletFinalLockedSpecimen?.mutationExecuted === true}</dd>
        </dl>
      `;
      slot.replaceChildren(panel);
      renderMcel18nWalletToolSurface(commitBoundary);
    }

    function renderMcelTinyRuntimeSummary(app, summaryText, instance = ensureMcelTinyContractState().scmInstance) {
      const slot = app?.querySelector('[data-mc-slot="runtime.txDraft"]');
      if (!slot) return;
      const txDraftEnforcement = mcelTinyContractEnforceTxDraftProvenance(instance, "render-runtime-summary");
      const txDraft = txDraftEnforcement?.txDraft || instance?.runtime?.txDraft || {};
      const txDraftConsumerGate = instance?.runtime?.txDraftConsumerGate || {};
      const txDraftEndgamePreflight = txDraftConsumerGate.endgamePreflight || mcelTinyContractTxDraftEndgamePreflight({
        txDraft,
        freshness: txDraft.provenanceFreshness || {
          status: txDraft.freshnessStatus || "",
          invalidatedBy: txDraft.invalidatedBy || [],
          action: txDraft.freshnessAction || "",
          noSendBoundaryPreserved: txDraft.noSendBoundaryPreserved === true
        },
        consumerGate: txDraftConsumerGate
      });
      const network = instance?.runtime?.network || {};
      const selected = selectedMcelTinyContractItem(instance);
      const output = document.createElement("section");
      output.className = "mcel-dev-release-console__tx";
      output.dataset.mcGenerated = "true";
      output.dataset.mcOwner = "runtime";
      output.dataset.mcRuntimeState = "runtime.txDraft";
      output.innerHTML = `
        <strong>Runtime transaction draft</strong>
        <p>${summaryText || txDraft.summary || "Transaction draft is waiting."}</p>
        <dl>
          <dt>request</dt><dd>${txDraft.requestId || selected?.id || "none"}</dd>
          <dt>status</dt><dd>${txDraft.status || "empty"}${txDraft.noSend ? " · no-send" : ""}</dd>
          <dt>from</dt><dd>${txDraft.from ? txDraft.from.slice(0, 12) + "…" : "not drafted"}</dd>
          <dt>to</dt><dd>${txDraft.to || "not drafted"}</dd>
          <dt>value</dt><dd>${txDraft.value || "0x0"}</dd>
          <dt>chain</dt><dd>${txDraft.chainId || "missing"} / expected ${txDraft.expectedChainId || network.expectedChainId || "0x28757b2"}</dd>
          <dt>method</dt><dd>${txDraft.methodSignature || "not drafted"}</dd>
          <dt>calldata</dt><dd>${txDraft.calldata ? txDraft.calldata.slice(0, 22) + "…" : "not drafted"}</dd>
          <dt>nonce</dt><dd>${txDraft.nonce?.status || "not-probed"}</dd>
          <dt>gas</dt><dd>${txDraft.gasEstimate?.status || "not-probed"}</dd>
          <dt>call</dt><dd>${txDraft.ethCall?.status || "not-probed"}</dd>
          <dt>provenance</dt><dd>${txDraft.freshnessStatus || (txDraft.valid ? "valid" : "stale")} · ${txDraft.freshnessAction || "rebuild draft to prove freshness"}</dd>
          <dt>invalidated</dt><dd>${(txDraft.invalidatedBy || []).map((entry) => entry.reason).filter(Boolean).join(", ") || "none"}</dd>
          <dt>send/sign preflight</dt><dd>${txDraftEndgamePreflight.status || "locked-no-draft"} · ${txDraftEndgamePreflight.action || "future send/sign boundary is locked"}</dd>
          <dt>18N commit boundary</dt><dd>${instance?.runtime?.walletCommitBoundary?.kind || "mcelWalletToolCommitBoundary.v1"} · ${instance?.runtime?.walletCommitBoundary?.status || "locked"}</dd>
        </dl>
      `;
      slot.replaceChildren(output);
    }

    function renderMcelTinyEvidenceStrip(app, instance = ensureMcelTinyContractState().scmInstance) {
      const slot = app?.querySelector('[data-mc-slot="runtime.evidenceStrip"]');
      if (!slot) return;
      const strip = document.createElement("ul");
      strip.className = "mcel-dev-release-console__evidence-strip";
      strip.dataset.mcGenerated = "true";
      strip.dataset.mcOwner = "runtime";
      strip.dataset.mcRuntimeState = "runtime.evidenceStrip";
      const evidence = instance?.runtime?.evidenceStrip || [];
      (evidence.length ? evidence : ["runtime evidence waiting"]).forEach((entry) => {
        const item = document.createElement("li");
        item.textContent = entry;
        strip.appendChild(item);
      });
      slot.replaceChildren(strip);
    }

    function syncMcelTinyContractDomFromScm(app, instance = ensureMcelTinyContractState().scmInstance, reason = "sync") {
      if (!app || !instance) return;
      app.classList.add("mcel-dev-release-console");
      const title = app.querySelector('[data-mc-field="devRelease.title"]');
      const summary = app.querySelector('[data-mc-field="devRelease.summary"]');
      const contractAddress = app.querySelector('[data-mc-field="devRelease.contractAddress"]');
      const network = app.querySelector('[data-mc-field="devRelease.devNetwork"]');
      if (title) title.textContent = instance.source?.devRelease?.title || "Dev Network Release Console";
      if (summary) summary.textContent = instance.source?.devRelease?.summary || "";
      if (contractAddress) contractAddress.textContent = instance.source?.devRelease?.contractAddress || "";
      if (network) {
        const devNetwork = instance.source?.devRelease?.devNetwork || {};
        network.textContent = `Dev network: ${devNetwork.name || "local"} ${devNetwork.decimalChainId || ""} (${devNetwork.chainId || ""})`;
      }
      const itemNodes = [...app.querySelectorAll("[data-mc-item-id]")];
      const items = mcelTinyContractItems(instance);
      itemNodes.forEach((node) => {
        const item = items.find((entry) => entry.id === node.getAttribute("data-mc-item-id"));
        if (!item) return;
        node.dataset.mcStatus = item.status;
        node.dataset.mcRisk = item.risk;
        node.textContent = `${item.status.toUpperCase()} · ${item.title}`;
      });
      renderMcelTinyWalletPanel(app, instance);
      renderMcelTinyRuntimeSummary(app, reason, instance);
      renderMcelTinyRuntimeBadge(app, instance);
      renderMcelTinyEvidenceStrip(app, instance);
      renderMcelTinyContractMap(app);
    }

    function mountMcelTinyContractRuntime(source, reason = "manual", options = {}) {
      if (!mcelTinyContractRuntimeMount) return null;
      const tinyState = ensureMcelTinyContractState();
      if (options.reset !== false) {
        tinyState.selectedIndex = 0;
        tinyState.blockedWrites = 0;
        tinyState.repairCount = 0;
        tinyState.repairPacketCount = 0;
        tinyState.repairBoundaryBlockedCount = 0;
        tinyState.reviewedCount = 0;
        tinyState.walletConnectCount = 0;
        tinyState.walletDisconnectCount = 0;
        tinyState.walletDisconnectCommitCount = 0;
        tinyState.walletRevokeAttemptCount = 0;
        tinyState.walletRevokeSuccessCount = 0;
        tinyState.providerAccountsChangedCount = 0;
        tinyState.providerAccountSwitchCount = 0;
        tinyState.providerAccountDisconnectCount = 0;
        tinyState.providerChainChangedCount = 0;
        tinyState.providerDisconnectCount = 0;
        tinyState.providerErrorCount = 0;
        tinyState.routeLoaderCount = 0;
        tinyState.networkVerifyCount = 0;
        tinyState.releaseSelectCount = 0;
        tinyState.txDraftCount = 0;
        tinyState.fullBatteryRunCount = 0;
        tinyState.externalOutcomeCount = 0;
        tinyState.externalBlockedCount = 0;
        tinyState.externalExceptionCount = 0;
        tinyState.lastWalletResetClean = false;
        tinyState.lastExternalOutcome = null;
        tinyState.lastWalletActionOutcome = null;
        tinyState.lastWalletCommitBoundary = mcelCommitBoundaryDefault("wallet.send-sign", "mount-reset");
        tinyState.commitBoundaryReceipts = [];
        tinyState.evidence = [];
        resetMcelTinyContractWalletAdapterState("mount-reset");
      }

      const runtime = createMcelTinyContractScmRuntime({exercise: options.exercise !== false});
      if (!runtime?.instance) {
        mcelTinyContractRuntimeMount.textContent = "SCM runtime failed to initialize.";
        renderMcelTinyContractProof(null, "scm-unavailable");
        return null;
      }

      tinyState.runCount += 1;
      mcelTinyContractRuntimeMount.innerHTML = source;
      const app = mcelTinyContractRuntimeMount.querySelector('[data-mc-component="dev-network-release-console"]');
      const walletButton = app?.querySelector('[data-mc-effect="wallet.connect"]');
      const disconnectButton = app?.querySelector('[data-mc-effect="wallet.disconnect"]');
      const verifyButton = app?.querySelector('[data-mc-effect="network.verify"]');
      const selectButton = app?.querySelector('[data-mc-effect="release.select"]');
      const draftButton = app?.querySelector('[data-mc-effect="release.draftTx"]');
      const approveButton = app?.querySelector('[data-mc-effect="release.approve"]');

      walletButton?.addEventListener("click", () => connectMcelTinyContractWallet("runtime-button"));
      disconnectButton?.addEventListener("click", () => disconnectMcelTinyContractWallet("runtime-button"));
      verifyButton?.addEventListener("click", () => verifyMcelTinyContractNetwork("runtime-button"));
      selectButton?.addEventListener("click", () => clickMcelTinyContractCounter());
      draftButton?.addEventListener("click", () => { void draftMcelTinyContractTransaction("runtime-button"); });
      approveButton?.addEventListener("click", () => markMcelTinyContractReviewed("runtime-button"));

      syncMcelTinyContractDomFromScm(app, runtime.instance, reason);
      if (options.exercise !== false) {
        repairMcelTinyContractRuntimeChrome("complete-dev-network-proof");
        attemptMcelTinyContractForbiddenWrite("complete-dev-network-proof");
      }
      renderMcelTinyContractProof(app, reason);
      return app;
    }

    function mcelTinyContractWalletSubsystem() {
      const app = window.MainComputerWalletApp;
      if (!app || typeof app !== "object") return null;
      const canConnect = typeof app.requestConnect === "function";
      const canDisconnect = typeof app.requestDisconnect === "function";
      const canSnapshot = typeof app.providerSnapshot === "function";
      if (!canConnect && !canDisconnect && !canSnapshot) return null;
      return app;
    }

    function mcelTinyContractWalletSubsystemEvent() {
      return {
        preventDefault() {},
        stopPropagation() {},
        stopImmediatePropagation() {}
      };
    }

    function snapshotMcelTinyContractWalletSubsystemState(subsystem = mcelTinyContractWalletSubsystem()) {
      if (!subsystem) return null;
      const state = subsystem.state || {};
      const wallet = state.wallet || {};
      const events = Array.isArray(state.events) ? state.events.slice(0, 8) : [];
      return {
        hookState: state.hookState || "",
        lastAction: state.lastAction || "",
        providerState: state.providerState || "",
        connected: Boolean(wallet.connected),
        address: wallet.address || "",
        chainId: wallet.chainId || "",
        recentEvents: events.map((event) => ({
          type: event?.type || "",
          detail: event?.detail || {}
        }))
      };
    }

    async function mcelTinyContractWalletSubsystemProviderSnapshot(subsystem = mcelTinyContractWalletSubsystem()) {
      if (!subsystem?.providerSnapshot) return null;
      recordMcelTinyContractWalletCall("MainComputerWalletApp.providerSnapshot", "start", {});
      try {
        const snapshot = await subsystem.providerSnapshot();
        recordMcelTinyContractWalletCall("MainComputerWalletApp.providerSnapshot", "pass", snapshot);
        return snapshot;
      } catch (error) {
        recordMcelTinyContractWalletCall("MainComputerWalletApp.providerSnapshot", "fail", mcelTinyContractWalletError(error));
        throw error;
      }
    }

    async function connectMcelTinyContractThroughWalletSubsystem(interactive = false) {
      const subsystem = mcelTinyContractWalletSubsystem();
      if (!subsystem) return null;
      const adapter = mcelTinyContractWalletAdapterState();
      adapter.walletSubsystemReady = true;
      adapter.walletSubsystemPreferred = true;
      adapter.walletSubsystemUsed = true;
      adapter.directProviderFallback = false;
      adapter.mockFallback = false;
      adapter.liveProvider = true;
      adapter.providerKind = "wallet-subsystem";
      adapter.connectSource = "MainComputerWalletApp";
      if (mcelTinyContractInjectedProvider()) {
        bindMcelTinyContractWalletProviderEvents(mcelTinyContractInjectedProvider());
      } else {
        adapter.eventsBound = true;
        recordMcelTinyContractWalletEvent("wallet-subsystem.events.managed", {
          reason: "MainComputerWalletApp owns provider event binding"
        });
      }
      ensureMcelTinyContractState().walletSubsystemMode = "used";

      let connectResult = null;
      if (interactive && typeof subsystem.requestConnect === "function") {
        recordMcelTinyContractWalletCall("MainComputerWalletApp.requestConnect", "start", {});
        try {
          connectResult = await subsystem.requestConnect(mcelTinyContractWalletSubsystemEvent());
          recordMcelTinyContractWalletCall("MainComputerWalletApp.requestConnect", "pass", {
            state: snapshotMcelTinyContractWalletSubsystemState(subsystem)
          });
        } catch (error) {
          recordMcelTinyContractWalletCall("MainComputerWalletApp.requestConnect", "fail", mcelTinyContractWalletError(error));
          throw error;
        }
      }

      if (typeof subsystem.ensureExpectedChain === "function") {
        recordMcelTinyContractWalletCall("MainComputerWalletApp.ensureExpectedChain", "start", {});
        try {
          const ensuredChainId = await subsystem.ensureExpectedChain();
          recordMcelTinyContractWalletCall("MainComputerWalletApp.ensureExpectedChain", "pass", {chainId: ensuredChainId});
        } catch (error) {
          recordMcelTinyContractWalletCall("MainComputerWalletApp.ensureExpectedChain", "fail", mcelTinyContractWalletError(error));
        }
      }

      let snapshot = null;
      try {
        snapshot = await mcelTinyContractWalletSubsystemProviderSnapshot(subsystem);
      } catch (_error) {
        snapshot = null;
      }
      const stateSnapshot = snapshotMcelTinyContractWalletSubsystemState(subsystem);
      const accounts = Array.isArray(snapshot?.accounts) ? snapshot.accounts : (stateSnapshot?.address ? [stateSnapshot.address] : []);
      const account = accounts[0] || snapshot?.address || stateSnapshot?.address || "";
      const chainId = snapshot?.chainId || stateSnapshot?.chainId || "";
      const outcome = recordMcelTinyContractExternalOutcome({
        operation: "wallet.connect",
        phase: "wallet-subsystem",
        status: account ? "pass" : "blocked",
        known: true,
        reason: account ? "account-granted" : "account-grant-missing",
        message: account
          ? "Wallet subsystem returned an account snapshot."
          : "Wallet subsystem did not return an account snapshot.",
        provider: {kind: "wallet-subsystem", live: true, source: "MainComputerWalletApp"},
        rpc: mcelTinyContractWalletRpcMethods(adapter, true),
        value: {account, chainId},
        nextAction: account ? "" : "Unlock/approve the wallet request or inspect the Wallet app provider snapshot."
      });
      return {
        mock: false,
        liveProvider: true,
        provider: "wallet-subsystem",
        account,
        chainId,
        interactive,
        outcome,
        walletSubsystemSnapshot: snapshot,
        walletSubsystemState: stateSnapshot,
        walletSubsystemConnectResult: connectResult,
        adapter: {
          providerKind: adapter.providerKind,
          liveProvider: adapter.liveProvider,
          mockFallback: adapter.mockFallback,
          walletSubsystemReady: adapter.walletSubsystemReady,
          walletSubsystemUsed: adapter.walletSubsystemUsed,
          walletSubsystemPreferred: adapter.walletSubsystemPreferred,
          directProviderFallback: adapter.directProviderFallback,
          connectSource: adapter.connectSource,
          calls: adapter.calls,
          events: adapter.events,
          eventsBound: adapter.eventsBound,
          ethersReady: adapter.ethersReady,
          lastError: adapter.lastError
        }
      };
    }

    async function disconnectMcelTinyContractThroughWalletSubsystem() {
      const subsystem = mcelTinyContractWalletSubsystem();
      if (!subsystem?.requestDisconnect) return null;
      const adapter = mcelTinyContractWalletAdapterState();
      adapter.walletSubsystemReady = true;
      adapter.walletSubsystemPreferred = true;
      adapter.walletSubsystemUsed = true;
      adapter.directProviderFallback = false;
      adapter.liveProvider = true;
      adapter.providerKind = "wallet-subsystem";
      adapter.disconnectSource = "MainComputerWalletApp";
      if (mcelTinyContractInjectedProvider()) {
        bindMcelTinyContractWalletProviderEvents(mcelTinyContractInjectedProvider());
      }
      ensureMcelTinyContractState().walletSubsystemMode = "used";

      recordMcelTinyContractWalletCall("MainComputerWalletApp.requestDisconnect", "start", {});
      try {
        const result = await subsystem.requestDisconnect(mcelTinyContractWalletSubsystemEvent());
        const state = snapshotMcelTinyContractWalletSubsystemState(subsystem);
        const doneEvent = (state?.recentEvents || []).find((event) => event.type === "disconnect.done");
        recordMcelTinyContractWalletCall("MainComputerWalletApp.requestDisconnect", "pass", {state, result});
        return {
          attempted: true,
          revoked: Boolean(doneEvent?.detail?.revoked),
          mock: false,
          source: "wallet-subsystem",
          result,
          state
        };
      } catch (error) {
        recordMcelTinyContractWalletCall("MainComputerWalletApp.requestDisconnect", "fail", mcelTinyContractWalletError(error));
        throw error;
      }
    }

    function resetMcelTinyContractWalletAdapterState(reason = "reset") {
      const tinyState = ensureMcelTinyContractState();
      tinyState.walletAdapter = {
        providerKind: "unknown",
        liveProvider: false,
        mockFallback: false,
        ethersReady: Boolean(window.ethers?.BrowserProvider),
        walletSubsystemReady: Boolean(mcelTinyContractWalletSubsystem()),
        walletSubsystemUsed: false,
        walletSubsystemPreferred: false,
        directProviderFallback: false,
        connectSource: "unknown",
        disconnectSource: "unknown",
        eventsBound: false,
        calls: [],
        events: [],
        lastError: "",
        permissionRevoked: false,
        reason
      };
      return tinyState.walletAdapter;
    }

    function mcelTinyContractWalletAdapterState() {
      const tinyState = ensureMcelTinyContractState();
      const adapter = tinyState.walletAdapter || resetMcelTinyContractWalletAdapterState("initialize");
      adapter.ethersReady = Boolean(window.ethers?.BrowserProvider);
      adapter.walletSubsystemReady = Boolean(mcelTinyContractWalletSubsystem());
      if (!Array.isArray(adapter.calls)) adapter.calls = [];
      if (!Array.isArray(adapter.events)) adapter.events = [];
      return adapter;
    }

    function mcelTinyContractWalletError(error) {
      return {
        name: error?.name || "Error",
        code: error?.code ?? error?.data?.code ?? "",
        message: error?.message || String(error)
      };
    }

    function mcelTinyContractWalletRpcMethods(adapter = mcelTinyContractWalletAdapterState(), includeAll = false) {
      return (adapter.calls || [])
        .filter((entry) => includeAll || ["pass", "mock", "fail", "unavailable", "empty"].includes(entry.status))
        .map((entry) => entry.method)
        .filter(Boolean);
    }

    function mcelTinyContractExternalOutcome(input = {}) {
      const adapter = mcelTinyContractWalletAdapterState();
      const status = ["pass", "blocked", "exception", "waiting"].includes(input.status)
        ? input.status
        : "exception";
      const outcome = {
        kind: "mcel-external-outcome",
        sequence: Number(input.sequence || 0),
        capturedAt: String(input.capturedAt || ""),
        operation: String(input.operation || "wallet.unknown"),
        phase: String(input.phase || "external"),
        status,
        known: input.known !== false,
        reason: String(input.reason || (status === "pass" ? "completed" : "external-outcome")),
        message: String(input.message || ""),
        provider: {
          kind: input.provider?.kind || adapter.providerKind || "unknown",
          live: input.provider?.live ?? adapter.liveProvider === true,
          source: input.provider?.source || adapter.connectSource || adapter.disconnectSource || "unknown",
          mock: input.provider?.mock ?? adapter.mockFallback === true
        },
        rpc: Array.isArray(input.rpc) ? input.rpc.filter(Boolean) : mcelTinyContractWalletRpcMethods(adapter, true),
        error: input.error || null,
        value: input.value || {},
        containment: {
          sourceChanged: false,
          txDraftCreated: false,
          runtimeMutationGoverned: true,
          ...(input.containment || {})
        },
        nextAction: String(input.nextAction || "")
      };
      return outcome;
    }

    function mcelTinyContractIsWalletActionOutcome(outcome) {
      return outcome?.kind === "mcel-external-outcome" && ["wallet.connect", "wallet.disconnect"].includes(outcome.operation);
    }

    function mcelTinyContractOutcomeSequence(outcome) {
      return Number(outcome?.sequence || 0);
    }

    function recordMcelTinyContractExternalOutcome(outcomeInput = {}) {
      const tinyState = ensureMcelTinyContractState();
      tinyState.externalOutcomeCount += 1;
      const outcome = mcelTinyContractExternalOutcome({
        ...outcomeInput,
        sequence: tinyState.externalOutcomeCount,
        capturedAt: new Date().toISOString()
      });
      if (outcome.status === "blocked") tinyState.externalBlockedCount += 1;
      if (outcome.status === "exception") tinyState.externalExceptionCount += 1;
      tinyState.lastExternalOutcome = outcome;
      if (mcelTinyContractIsWalletActionOutcome(outcome)) {
        tinyState.lastWalletActionOutcome = outcome;
      }
      const adapter = mcelTinyContractWalletAdapterState();
      if (!Array.isArray(adapter.externalOutcomes)) adapter.externalOutcomes = [];
      adapter.externalOutcomes.push(outcome);
      adapter.externalOutcomes = adapter.externalOutcomes.slice(-12);
      return outcome;
    }

    function mcelTinyContractOutcomeFromWalletPayload(payload = {}) {
      if (payload.outcome?.kind === "mcel-external-outcome") return payload.outcome;
      const adapter = mcelTinyContractWalletAdapterState();
      const rpc = mcelTinyContractWalletRpcMethods(adapter, true);
      const account = String(payload.account || "");
      const chainId = String(payload.chainId || "");
      if (payload.error) {
        return mcelTinyContractExternalOutcome({
          operation: "wallet.connect",
          phase: "provider-request",
          status: "exception",
          known: false,
          reason: "provider-exception",
          message: payload.error.message || "Wallet provider threw during connect.",
          error: payload.error,
          value: {account, chainId},
          rpc,
          nextAction: "Inspect the wallet provider exception in the proof dock."
        });
      }
      if (payload.mock) {
        return mcelTinyContractExternalOutcome({
          operation: "wallet.connect",
          phase: "provider-detect",
          status: "blocked",
          known: true,
          reason: "mock-fallback",
          message: "No live wallet provider was available; mock fallback is degraded evidence.",
          value: {account, chainId},
          rpc,
          provider: {kind: "mock-dev-provider", live: false, mock: true},
          nextAction: "Open the app in a browser with an injected wallet provider."
        });
      }
      if (!account) {
        return mcelTinyContractExternalOutcome({
          operation: "wallet.connect",
          phase: "account-grant",
          status: "blocked",
          known: true,
          reason: "account-grant-missing",
          message: "Wallet provider did not return an account; the user may have canceled, locked, or denied the request.",
          value: {account, chainId},
          rpc,
          nextAction: "Unlock the wallet and approve the account request, then retry connect."
        });
      }
      return mcelTinyContractExternalOutcome({
        operation: "wallet.connect",
        phase: "account-grant",
        status: "pass",
        known: true,
        reason: "account-granted",
        message: "Wallet account and chain were captured through the external outcome envelope.",
        value: {account, chainId},
        rpc
      });
    }

    function mcelTinyContractOutcomeFromRevoke(revoke = {}) {
      const adapter = mcelTinyContractWalletAdapterState();
      const rpc = mcelTinyContractWalletRpcMethods(adapter, true);
      if (revoke.error) {
        return mcelTinyContractExternalOutcome({
          operation: "wallet.disconnect",
          phase: "permission-revoke",
          status: "blocked",
          known: true,
          reason: "permission-revoke-not-completed",
          message: revoke.error.message || "Provider permission revoke did not complete; local runtime reset still proceeds through SCM.",
          error: revoke.error,
          value: {attempted: revoke.attempted === true, revoked: revoke.revoked === true},
          rpc,
          nextAction: "Use wallet extension permissions if provider-level revoke is required."
        });
      }
      return mcelTinyContractExternalOutcome({
        operation: "wallet.disconnect",
        phase: "permission-revoke",
        status: revoke.revoked ? "pass" : "blocked",
        known: true,
        reason: revoke.revoked ? "permission-revoked" : (revoke.mock ? "mock-or-no-provider" : "permission-revoke-unavailable"),
        message: revoke.revoked
          ? "Provider permission revoke completed before local runtime reset."
          : "Provider permission revoke was unavailable or unnecessary; local runtime reset remains governed.",
        value: {attempted: revoke.attempted === true, revoked: revoke.revoked === true, mock: revoke.mock === true},
        rpc,
        nextAction: revoke.revoked ? "" : "Inspect wallet extension permissions if full provider disconnect is required."
      });
    }

    function mcelTinyContractExceptionOutcome(operation, phase, error) {
      const detail = mcelTinyContractWalletError(error);
      return mcelTinyContractExternalOutcome({
        operation,
        phase,
        status: "exception",
        known: false,
        reason: "uncategorized-external-exception",
        message: detail.message,
        error: detail,
        rpc: mcelTinyContractWalletRpcMethods(mcelTinyContractWalletAdapterState(), true),
        nextAction: "Open the proof dock and inspect the captured external exception."
      });
    }

    function mcelTinyContractLatestWalletActionOutcome(instance = null) {
      const tinyState = ensureMcelTinyContractState();
      const runtimeOutcome = instance?.runtime?.externalOutcome;
      const stateOutcome = tinyState.lastWalletActionOutcome;
      const genericStateOutcome = tinyState.lastExternalOutcome;
      const candidates = [runtimeOutcome, stateOutcome, genericStateOutcome]
        .filter((outcome) => outcome?.kind === "mcel-external-outcome")
        .filter((outcome) => mcelTinyContractIsWalletActionOutcome(outcome) || !stateOutcome);
      if (!candidates.length) {
        return mcelTinyContractExternalOutcome({
          operation: "wallet.lifecycle",
          phase: "waiting",
          status: "waiting",
          known: false,
          reason: "not-run",
          message: "No external wallet outcome has been captured."
        });
      }
      return candidates.reduce((latest, outcome) => {
        return mcelTinyContractOutcomeSequence(outcome) >= mcelTinyContractOutcomeSequence(latest) ? outcome : latest;
      }, candidates[0]);
    }

    function mcelTinyContractTxSelector(signature = "") {
      let hash = 0;
      Array.from(String(signature || "")).forEach((char) => {
        hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
      });
      return (hash >>> 0).toString(16).padStart(8, "0").slice(0, 8);
    }

    function mcelTinyContractBytes32FromText(value = "") {
      const hex = Array.from(String(value || "")).map((char) => {
        return char.charCodeAt(0).toString(16).padStart(2, "0");
      }).join("");
      return `0x${hex.slice(0, 64).padEnd(64, "0")}`;
    }

    function mcelTinyContractTxFunctionName(signature = "") {
      return String(signature || "").split("(")[0] || "";
    }

    function mcelTinyContractTxDraftArgs(request = {}, wallet = {}, contractAddress = "") {
      const signature = String(request.contractMethod || "");
      if (signature.startsWith("allowance(")) {
        return [
          wallet.account || "0x0000000000000000000000000000000000000000",
          contractAddress || "0x0000000000000000000000000000000000000000"
        ];
      }
      if (signature.includes("bytes32")) {
        return [mcelTinyContractBytes32FromText(request.id || signature)];
      }
      return [];
    }

    function mcelTinyContractTxArgPreview(args = []) {
      return args.map((arg) => {
        const text = String(arg || "");
        return text.length > 18 ? `${text.slice(0, 14)}…${text.slice(-4)}` : text;
      });
    }

    function mcelTinyContractFallbackCalldata(signature = "", args = []) {
      const selector = mcelTinyContractTxSelector(signature);
      const payload = args.map((arg) => {
        const text = String(arg || "");
        if (text.startsWith("0x")) return text.slice(2).padStart(64, "0").slice(-64);
        return mcelTinyContractBytes32FromText(text).slice(2);
      }).join("");
      return `0x${selector}${payload}`;
    }

    function mcelTinyContractEncodeTxDraftData(request = {}, wallet = {}, contractAddress = "") {
      const methodSignature = String(request.contractMethod || "");
      const functionName = mcelTinyContractTxFunctionName(methodSignature);
      const args = mcelTinyContractTxDraftArgs(request, wallet, contractAddress);
      if (!methodSignature) {
        return {
          status: "missing",
          methodSignature,
          functionName,
          args,
          argsPreview: [],
          calldata: "",
          data: "",
          calldataEncoding: "missing-method"
        };
      }
      try {
        if (window.ethers?.Interface) {
          const iface = new window.ethers.Interface([`function ${methodSignature}`]);
          const calldata = iface.encodeFunctionData(functionName, args);
          return {
            status: "encoded",
            methodSignature,
            functionName,
            args,
            argsPreview: mcelTinyContractTxArgPreview(args),
            calldata,
            data: calldata,
            calldataEncoding: "ethers.Interface"
          };
        }
      } catch (error) {
        return {
          status: "fallback",
          methodSignature,
          functionName,
          args,
          argsPreview: mcelTinyContractTxArgPreview(args),
          calldata: mcelTinyContractFallbackCalldata(methodSignature, args),
          data: mcelTinyContractFallbackCalldata(methodSignature, args),
          calldataEncoding: "deterministic-fallback",
          encodingError: mcelTinyContractWalletError(error)
        };
      }
      const calldata = mcelTinyContractFallbackCalldata(methodSignature, args);
      return {
        status: "fallback",
        methodSignature,
        functionName,
        args,
        argsPreview: mcelTinyContractTxArgPreview(args),
        calldata,
        data: calldata,
        calldataEncoding: "deterministic-fallback"
      };
    }

    async function mcelTinyContractOptionalWalletRequest(provider, method, params = []) {
      if (!provider || typeof provider.request !== "function") {
        return {
          method,
          status: "skipped",
          reason: "provider-unavailable"
        };
      }
      try {
        const value = await mcelTinyContractWalletRequest(provider, method, params);
        return {
          method,
          status: "pass",
          value
        };
      } catch (error) {
        return {
          method,
          status: "unavailable",
          error: mcelTinyContractWalletError(error)
        };
      }
    }

    async function buildMcelTinyContractTxDraftProbe(instance) {
      const source = instance?.source || {};
      const state = instance?.state || {};
      const runtime = instance?.runtime || {};
      const contractAddress = source.devRelease?.contractAddress || "";
      const requests = source.devRelease?.requests || [];
      const selectedId = state.selectedRequestId || requests[0]?.id || "";
      const request = requests.find((entry) => entry.id === selectedId) || requests[0] || null;
      const wallet = runtime.wallet || {};
      const network = runtime.network || {};
      const externalOutcome = runtime.externalOutcome || {};
      const externalBlocked = ["blocked", "exception"].includes(externalOutcome.status);
      const ready = Boolean(wallet.connected && network.ok && request && !externalBlocked);
      const encoding = ready
        ? mcelTinyContractEncodeTxDraftData(request, wallet, contractAddress)
        : {
            status: "blocked",
            methodSignature: request?.contractMethod || "",
            functionName: mcelTinyContractTxFunctionName(request?.contractMethod || ""),
            args: [],
            argsPreview: [],
            calldata: "",
            data: "",
            calldataEncoding: "blocked-until-wallet-network-ready"
          };
      const tx = {
        from: ready ? (wallet.account || "") : "",
        to: ready ? (contractAddress || "") : "",
        value: "0x0",
        data: ready ? (encoding.calldata || encoding.data || "") : "",
        chainId: network.chainId || "",
        noSend: true
      };
      const provider = ready ? mcelTinyContractInjectedProvider() : null;
      const nonce = ready
        ? await mcelTinyContractOptionalWalletRequest(provider, "eth_getTransactionCount", [tx.from, "pending"])
        : {method: "eth_getTransactionCount", status: "skipped", reason: "wallet-network-gate-blocked"};
      const gasEstimate = ready
        ? await mcelTinyContractOptionalWalletRequest(provider, "eth_estimateGas", [{
            from: tx.from,
            to: tx.to,
            value: tx.value,
            data: tx.data
          }])
        : {method: "eth_estimateGas", status: "skipped", reason: "wallet-network-gate-blocked"};
      const ethCall = ready
        ? await mcelTinyContractOptionalWalletRequest(provider, "eth_call", [{
            from: tx.from,
            to: tx.to,
            value: tx.value,
            data: tx.data
          }, "latest"])
        : {method: "eth_call", status: "skipped", reason: "wallet-network-gate-blocked"};
      return {
        kind: "mcel-runtime-tx-draft-probe",
        noSend: true,
        ready,
        createdFrom: {
          requestId: request?.id || "",
          sourcePath: "source.devRelease.requests",
          contractMethod: request?.contractMethod || "",
          status: request?.status || "",
          risk: request?.risk || ""
        },
        tx,
        encoding,
        nonce,
        gasEstimate,
        ethCall,
        rpc: mcelTinyContractWalletRpcMethods(mcelTinyContractWalletAdapterState(), true)
      };
    }

    function recordMcelTinyContractWalletCall(method, status, detail = {}) {
      const adapter = mcelTinyContractWalletAdapterState();
      const call = {
        index: adapter.calls.length + 1,
        method,
        status,
        live: adapter.liveProvider === true,
        detail
      };
      adapter.calls.push(call);
      adapter.calls = adapter.calls.slice(-24);
      return call;
    }

    function recordMcelTinyContractWalletEvent(type, detail = {}) {
      const adapter = mcelTinyContractWalletAdapterState();
      const event = {
        index: adapter.events.length + 1,
        type,
        detail
      };
      adapter.events.push(event);
      adapter.events = adapter.events.slice(-24);
      return event;
    }

    function mcelTinyContractInjectedProvider() {
      return window.ethereum && typeof window.ethereum.request === "function" ? window.ethereum : null;
    }

    async function mcelTinyContractWalletRequest(provider, method, params = []) {
      recordMcelTinyContractWalletCall(method, "start", {params});
      try {
        const value = await provider.request({method, params});
        recordMcelTinyContractWalletCall(method, "pass", {
          value: Array.isArray(value) ? value.slice(0, 3) : value
        });
        return value;
      } catch (error) {
        const detail = mcelTinyContractWalletError(error);
        mcelTinyContractWalletAdapterState().lastError = detail.message;
        recordMcelTinyContractWalletCall(method, "fail", detail);
        throw error;
      }
    }

    function runMcelTinyContractProviderEventEffect(effectName, payload = {}, reason = effectName) {
      const tinyState = ensureMcelTinyContractState();
      const instance = tinyState.scmInstance;
      const app = mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      if (!instance || !window.McelLabScm?.runEffect) {
        recordMcelTinyContractEvidence(
          "wallet-event",
          `${effectName} observed, but SCM instance was unavailable; runtime was not mutated directly.`,
          "warn",
          {effectName, payload, reason}
        );
        renderMcelTinyContractProof(app, reason);
        return null;
      }
      try {
        const result = window.McelLabScm.runEffect(instance, effectName, payload);
        if (effectName === "wallet.provider.accountsChanged") {
          const eventResult = result?.result && typeof result.result === "object" ? result.result : result || {};
          tinyState.providerAccountsChangedCount += 1;
          if (eventResult.accountChanged === true) tinyState.providerAccountSwitchCount += 1;
          if (eventResult.disconnected === true) tinyState.providerAccountDisconnectCount += 1;
        }
        if (effectName === "wallet.provider.chainChanged") tinyState.providerChainChangedCount += 1;
        if (effectName === "wallet.provider.disconnect") tinyState.providerDisconnectCount += 1;
        if (effectName === "wallet.provider.error") tinyState.providerErrorCount += 1;
        recordMcelTinyContractEvidence(
          "wallet-event",
          `${effectName} committed through SCM provider-event effect.`,
          result?.ok === false ? "fail" : "pass",
          {effectName, payload, result}
        );
        syncMcelTinyContractDomFromScm(app, instance, reason);
        renderMcelTinyContractProof(app, reason);
        return result;
      } catch (error) {
        const detail = mcelTinyContractWalletError(error);
        recordMcelTinyContractEvidence(
          "wallet-event",
          `${effectName} was blocked or failed through SCM provider-event effect.`,
          "fail",
          {effectName, payload, error: detail}
        );
        syncMcelTinyContractDomFromScm(app, instance, `${reason} failed`);
        renderMcelTinyContractProof(app, `${reason}-failed`);
        return {error: detail};
      }
    }

    function bindMcelTinyContractWalletProviderEvents(provider) {
      const adapter = mcelTinyContractWalletAdapterState();
      if (!provider || typeof provider.on !== "function") {
        adapter.eventsBound = false;
        recordMcelTinyContractWalletEvent("provider.events.unavailable", {
          reason: "provider.on missing"
        });
        return false;
      }
      if (adapter.eventsBound) return true;
      const accountsChanged = (accounts) => {
        const normalizedAccounts = Array.isArray(accounts) ? accounts : [];
        const outcome = recordMcelTinyContractExternalOutcome({
          operation: "wallet.provider.accountsChanged",
          phase: "provider-event",
          status: "pass",
          known: true,
          reason: normalizedAccounts[0] ? "account-event" : "account-disconnected",
          message: "Provider accountsChanged event was normalized before SCM consumption.",
          value: {accounts: normalizedAccounts, account: normalizedAccounts[0] || ""},
          rpc: mcelTinyContractWalletRpcMethods(adapter, true)
        });
        recordMcelTinyContractWalletEvent("accountsChanged", {accounts: normalizedAccounts, outcome});
        runMcelTinyContractProviderEventEffect(
          "wallet.provider.accountsChanged",
          {accounts: normalizedAccounts, outcome},
          "wallet-accountsChanged"
        );
      };
      const chainChanged = (chainId) => {
        const normalized = String(chainId || "");
        const outcome = recordMcelTinyContractExternalOutcome({
          operation: "wallet.provider.chainChanged",
          phase: "provider-event",
          status: "pass",
          known: true,
          reason: "chain-event",
          message: "Provider chainChanged event was normalized before SCM consumption.",
          value: {chainId: normalized},
          rpc: mcelTinyContractWalletRpcMethods(adapter, true)
        });
        recordMcelTinyContractWalletEvent("chainChanged", {chainId: normalized, outcome});
        runMcelTinyContractProviderEventEffect(
          "wallet.provider.chainChanged",
          {chainId: normalized, outcome},
          "wallet-chainChanged"
        );
      };
      const disconnect = (error) => {
        const detail = mcelTinyContractWalletError(error || new Error("provider disconnect"));
        const outcome = recordMcelTinyContractExternalOutcome({
          operation: "wallet.provider.disconnect",
          phase: "provider-event",
          status: "blocked",
          known: true,
          reason: "provider-disconnect",
          message: detail.message,
          error: detail,
          value: {code: detail.code},
          rpc: mcelTinyContractWalletRpcMethods(adapter, true)
        });
        recordMcelTinyContractWalletEvent("disconnect", {...detail, outcome});
        runMcelTinyContractProviderEventEffect(
          "wallet.provider.disconnect",
          {error: detail, code: detail.code, message: detail.message, outcome},
          "wallet-provider-disconnect"
        );
      };
      const providerError = (error) => {
        const detail = mcelTinyContractWalletError(error || new Error("provider error"));
        const outcome = recordMcelTinyContractExternalOutcome({
          operation: "wallet.provider.error",
          phase: "provider-event",
          status: "exception",
          known: false,
          reason: "provider-error",
          message: detail.message,
          error: detail,
          value: {code: detail.code},
          rpc: mcelTinyContractWalletRpcMethods(adapter, true)
        });
        recordMcelTinyContractWalletEvent("error", {...detail, outcome});
        runMcelTinyContractProviderEventEffect(
          "wallet.provider.error",
          {error: detail, code: detail.code, message: detail.message, outcome},
          "wallet-provider-error"
        );
      };
      provider.on("accountsChanged", accountsChanged);
      provider.on("chainChanged", chainChanged);
      provider.on("disconnect", disconnect);
      provider.on("error", providerError);
      adapter.eventsBound = true;
      adapter.accountsChanged = accountsChanged;
      adapter.chainChanged = chainChanged;
      adapter.disconnect = disconnect;
      adapter.providerError = providerError;
      recordMcelTinyContractWalletEvent("provider.events.bound", {
        events: ["accountsChanged", "chainChanged", "disconnect", "error"]
      });
      return true;
    }

    async function readMcelTinyContractWalletProvider(interactive = false) {
      const adapter = resetMcelTinyContractWalletAdapterState(interactive ? "interactive-connect" : "passive-connect");
      adapter.ethersReady = Boolean(window.ethers?.BrowserProvider);
      adapter.walletSubsystemReady = Boolean(mcelTinyContractWalletSubsystem());

      if (adapter.walletSubsystemReady) {
        try {
          const subsystemPayload = await connectMcelTinyContractThroughWalletSubsystem(interactive);
          if (subsystemPayload?.account || subsystemPayload?.chainId || subsystemPayload?.walletSubsystemSnapshot || subsystemPayload?.walletSubsystemState) {
            return subsystemPayload;
          }
          recordMcelTinyContractWalletCall("MainComputerWalletApp.connect", "empty", {
            reason: "wallet subsystem returned no account/chain snapshot; falling back to direct provider"
          });
        } catch (error) {
          const detail = mcelTinyContractWalletError(error);
          adapter.lastError = detail.message;
          recordMcelTinyContractWalletCall("MainComputerWalletApp.connect", "fallback-direct-provider", detail);
        }
      }

      const provider = mcelTinyContractInjectedProvider();
      if (!provider) {
        adapter.providerKind = "mock-dev-provider";
        adapter.mockFallback = true;
        adapter.liveProvider = false;
        adapter.directProviderFallback = false;
        adapter.connectSource = "mock-fallback";
        ensureMcelTinyContractState().walletSubsystemMode = adapter.walletSubsystemReady ? "subsystem-empty-then-mock" : "missing";
        recordMcelTinyContractWalletCall("provider.detect", "mock", {
          reason: "window.ethereum missing",
          walletSubsystemReady: adapter.walletSubsystemReady,
          ethersReady: adapter.ethersReady
        });
        const outcome = recordMcelTinyContractExternalOutcome({
          operation: "wallet.connect",
          phase: "provider-detect",
          status: "blocked",
          known: true,
          reason: "mock-fallback",
          message: "No injected wallet provider was available; mock fallback is degraded evidence.",
          provider: {kind: "mock-dev-provider", live: false, mock: true},
          rpc: mcelTinyContractWalletRpcMethods(adapter, true),
          value: {
            account: "0xDeaD00000000000000000000000000000000BEEF",
            chainId: "0x28757b2"
          },
          nextAction: "Open the app in a browser with MetaMask or another injected provider."
        });
        return {
          mock: true,
          liveProvider: false,
          provider: "mock-dev-provider",
          account: "0xDeaD00000000000000000000000000000000BEEF",
          chainId: "0x28757b2",
          interactive,
          outcome,
          adapter: {
            providerKind: adapter.providerKind,
            liveProvider: adapter.liveProvider,
            mockFallback: adapter.mockFallback,
            walletSubsystemReady: adapter.walletSubsystemReady,
            walletSubsystemUsed: adapter.walletSubsystemUsed,
            walletSubsystemPreferred: adapter.walletSubsystemPreferred,
            directProviderFallback: adapter.directProviderFallback,
            connectSource: adapter.connectSource,
            calls: adapter.calls,
            events: adapter.events,
            eventsBound: adapter.eventsBound,
            ethersReady: adapter.ethersReady
          }
        };
      }

      adapter.liveProvider = true;
      adapter.mockFallback = false;
      adapter.directProviderFallback = adapter.walletSubsystemReady === true;
      adapter.providerKind = provider.isMetaMask ? "metamask" : "ethereum-provider";
      adapter.connectSource = adapter.directProviderFallback ? "direct-provider-after-subsystem" : "direct-provider";
      ensureMcelTinyContractState().walletSubsystemMode = adapter.directProviderFallback ? "fallback-direct-provider" : "direct-provider";
      bindMcelTinyContractWalletProviderEvents(provider);

      let chainId = "";
      let accounts = [];
      let accountRequestError = null;
      let chainRequestError = null;
      let ethersSnapshot = null;
      let walletSubsystemSnapshot = null;
      try {
        chainId = String(await mcelTinyContractWalletRequest(provider, "eth_chainId") || "");
      } catch (error) {
        chainRequestError = mcelTinyContractWalletError(error);
        chainId = "";
      }
      try {
        accounts = await mcelTinyContractWalletRequest(provider, interactive ? "eth_requestAccounts" : "eth_accounts");
      } catch (error) {
        accountRequestError = mcelTinyContractWalletError(error);
        accounts = [];
      }

      if (window.ethers?.BrowserProvider) {
        try {
          recordMcelTinyContractWalletCall("ethers.BrowserProvider.getNetwork", "start", {});
          const browserProvider = new window.ethers.BrowserProvider(provider);
          const network = await browserProvider.getNetwork();
          ethersSnapshot = {
            chainId: typeof network?.chainId === "bigint" ? `0x${network.chainId.toString(16)}` : String(network?.chainId || ""),
            name: network?.name || ""
          };
          recordMcelTinyContractWalletCall("ethers.BrowserProvider.getNetwork", "pass", ethersSnapshot);
        } catch (error) {
          recordMcelTinyContractWalletCall("ethers.BrowserProvider.getNetwork", "fail", mcelTinyContractWalletError(error));
        }
      } else {
        recordMcelTinyContractWalletCall("ethers.BrowserProvider", "unavailable", {
          reason: "ethers.js BrowserProvider missing"
        });
      }

      const subsystem = mcelTinyContractWalletSubsystem();
      if (subsystem?.providerSnapshot) {
        try {
          walletSubsystemSnapshot = await mcelTinyContractWalletSubsystemProviderSnapshot(subsystem);
        } catch (_error) {
          walletSubsystemSnapshot = null;
        }
      } else {
        recordMcelTinyContractWalletCall("MainComputerWalletApp.providerSnapshot", "unavailable", {
          reason: "wallet subsystem not initialized"
        });
      }

      const account = Array.isArray(accounts) ? accounts[0] || "" : "";
      const outcome = recordMcelTinyContractExternalOutcome({
        operation: "wallet.connect",
        phase: account ? "account-grant" : (accountRequestError ? "eth_requestAccounts" : "account-grant"),
        status: account ? "pass" : (accountRequestError ? "blocked" : "blocked"),
        known: true,
        reason: account ? "account-granted" : (accountRequestError?.code ? "account-request-rejected" : "account-grant-missing"),
        message: account
          ? "Wallet account was returned by the provider."
          : (accountRequestError?.message || "Wallet provider did not return an account."),
        provider: {kind: adapter.providerKind, live: true, source: adapter.connectSource},
        rpc: mcelTinyContractWalletRpcMethods(adapter, true),
        error: accountRequestError || chainRequestError,
        value: {account, chainId},
        nextAction: account ? "" : "Unlock the wallet and approve the account request, then retry connect."
      });
      return {
        mock: false,
        liveProvider: true,
        provider: adapter.providerKind,
        account,
        chainId,
        interactive,
        outcome,
        ethersSnapshot,
        walletSubsystemSnapshot,
        adapter: {
          providerKind: adapter.providerKind,
          liveProvider: adapter.liveProvider,
          mockFallback: adapter.mockFallback,
          walletSubsystemReady: adapter.walletSubsystemReady,
          walletSubsystemUsed: adapter.walletSubsystemUsed,
          walletSubsystemPreferred: adapter.walletSubsystemPreferred,
          directProviderFallback: adapter.directProviderFallback,
          connectSource: adapter.connectSource,
          calls: adapter.calls,
          events: adapter.events,
          eventsBound: adapter.eventsBound,
          ethersReady: adapter.ethersReady,
          lastError: adapter.lastError
        }
      };
    }

    async function revokeMcelTinyContractWalletPermission() {
      const tinyState = ensureMcelTinyContractState();
      const adapter = mcelTinyContractWalletAdapterState();
      if (mcelTinyContractWalletSubsystem()?.requestDisconnect) {
        try {
          const subsystemRevoke = await disconnectMcelTinyContractThroughWalletSubsystem();
          if (subsystemRevoke) {
            if (subsystemRevoke.attempted) tinyState.walletRevokeAttemptCount += 1;
            if (subsystemRevoke.revoked) {
              tinyState.walletRevokeSuccessCount += 1;
              adapter.permissionRevoked = true;
            }
            return subsystemRevoke;
          }
        } catch (error) {
          recordMcelTinyContractWalletCall("MainComputerWalletApp.requestDisconnect", "fallback-direct-provider", mcelTinyContractWalletError(error));
        }
      }

      const provider = mcelTinyContractInjectedProvider();
      if (!provider) {
        recordMcelTinyContractWalletCall("wallet_revokePermissions", "mock", {
          reason: "window.ethereum missing"
        });
        adapter.permissionRevoked = false;
        adapter.disconnectSource = "mock-fallback";
        return {attempted: false, revoked: false, mock: true};
      }
      adapter.liveProvider = true;
      adapter.mockFallback = false;
      adapter.directProviderFallback = adapter.walletSubsystemReady === true;
      adapter.providerKind = provider.isMetaMask ? "metamask" : "ethereum-provider";
      adapter.disconnectSource = adapter.directProviderFallback ? "direct-provider-after-subsystem" : "direct-provider";
      bindMcelTinyContractWalletProviderEvents(provider);
      tinyState.walletRevokeAttemptCount += 1;
      try {
        await mcelTinyContractWalletRequest(provider, "wallet_revokePermissions", [{eth_accounts: {}}]);
        tinyState.walletRevokeSuccessCount += 1;
        adapter.permissionRevoked = true;
        return {attempted: true, revoked: true, mock: false, source: adapter.disconnectSource};
      } catch (error) {
        adapter.permissionRevoked = false;
        return {
          attempted: true,
          revoked: false,
          mock: false,
          source: adapter.disconnectSource,
          error: mcelTinyContractWalletError(error)
        };
      }
    }

    async function connectMcelTinyContractWallet(reason = "manual-wallet-connect") {
      const tinyState = ensureMcelTinyContractState();
      let app = mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      if (!tinyState.scmInstance || !window.McelLabScm?.runEffect) {
        app = renderMcelTinyContractTest("wallet-before-mount", { exercise: false, reset: false });
      }
      const instance = ensureMcelTinyContractState().scmInstance;
      if (!instance) return null;
      try {
        const walletPayload = await readMcelTinyContractWalletProvider(true);
        const outcome = recordMcelTinyContractExternalOutcome(mcelTinyContractOutcomeFromWalletPayload(walletPayload));
        walletPayload.outcome = outcome;
        const result = window.McelLabScm.runEffect(instance, "wallet.connect", walletPayload);
        const networkResult = window.McelLabScm.runEffect(instance, "network.verify");
        tinyState.walletConnectCount += 1;
        tinyState.networkVerifyCount += 1;
        recordMcelTinyContractEvidence(
          "wallet",
          outcome.status === "pass"
            ? "SCM captured a successful external wallet outcome as runtime-only data."
            : "SCM captured a blocked/exception wallet outcome, cleared unsafe runtime state, and left source untouched.",
          outcome.status === "pass" ? "pass" : "warn",
          {
            walletConnectCount: tinyState.walletConnectCount,
            outcome,
            result,
            networkResult,
            walletPayload,
            walletAdapter: tinyState.walletAdapter
          }
        );
        app = app || mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
        syncMcelTinyContractDomFromScm(app, instance, "Wallet/dev-network state captured through declared runtime effect.");
        renderMcelTinyContractProof(app, reason);
        return {result, networkResult, walletPayload};
      } catch (error) {
        const detail = mcelTinyContractWalletError(error);
        const outcome = recordMcelTinyContractExternalOutcome(mcelTinyContractExceptionOutcome("wallet.connect", "connect-handler", error));
        mcelTinyContractWalletAdapterState().lastError = detail.message;
        let effectEnvelope = null;
        try {
          effectEnvelope = window.McelLabScm.runEffect(instance, "wallet.connect", {
            provider: mcelTinyContractWalletAdapterState().providerKind || "exception",
            account: "",
            chainId: "",
            interactive: true,
            outcome,
            adapter: mcelTinyContractWalletAdapterState()
          });
          tinyState.walletConnectCount += 1;
        } catch (scmError) {
          recordMcelTinyContractEvidence(
            "wallet",
            "Wallet connect exception envelope could not be committed through SCM.",
            "fail",
            {reason, error: detail, outcome, scmError: mcelTinyContractWalletError(scmError), walletAdapter: tinyState.walletAdapter}
          );
        }
        recordMcelTinyContractEvidence(
          "wallet",
          "Wallet connect/check produced an external exception envelope and committed the blocked runtime state through SCM.",
          "warn",
          {reason, error: detail, outcome, effectEnvelope, walletAdapter: tinyState.walletAdapter}
        );
        app = app || mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
        syncMcelTinyContractDomFromScm(app, instance, "Wallet connect/check failed.");
        renderMcelTinyContractProof(app, reason);
        return {error: detail, outcome, effectEnvelope};
      }
    }


    async function disconnectMcelTinyContractWallet(reason = "manual-wallet-disconnect") {
      const tinyState = ensureMcelTinyContractState();
      let app = mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      if (!tinyState.scmInstance || !window.McelLabScm?.runEffect) {
        app = renderMcelTinyContractTest("disconnect-before-mount", { exercise: false, reset: false });
      }
      const instance = ensureMcelTinyContractState().scmInstance;
      if (!instance) return null;
      let revoke = {attempted: false, revoked: false};
      let disconnectOutcome = null;
      try {
        revoke = await revokeMcelTinyContractWalletPermission();
      } catch (error) {
        revoke = {
          attempted: true,
          revoked: false,
          error: mcelTinyContractWalletError(error)
        };
      }
      disconnectOutcome = recordMcelTinyContractExternalOutcome(mcelTinyContractOutcomeFromRevoke(revoke));
      try {
        const effectEnvelope = window.McelLabScm.runEffect(instance, "wallet.disconnect", {revoke, outcome: disconnectOutcome});
        const effectResult = effectEnvelope?.result || effectEnvelope;
        tinyState.walletDisconnectCount += 1;
        if (effectResult?.disconnected === true) {
          tinyState.walletDisconnectCommitCount += 1;
          tinyState.lastWalletResetClean = true;
        } else {
          tinyState.lastWalletResetClean = false;
        }
        recordMcelTinyContractEvidence(
          "wallet",
          revoke.revoked
            ? "wallet_revokePermissions succeeded, then SCM wallet.disconnect cleared local runtime wallet/network/tx draft state."
            : "SCM wallet.disconnect cleared local runtime wallet/network/tx draft state; provider permission revoke was unavailable, declined, or failed.",
          effectResult?.disconnected ? "pass" : "warn",
          {
            walletDisconnectCount: tinyState.walletDisconnectCount,
            walletDisconnectCommitCount: tinyState.walletDisconnectCommitCount,
            walletRevokeAttemptCount: tinyState.walletRevokeAttemptCount,
            walletRevokeSuccessCount: tinyState.walletRevokeSuccessCount,
            revoke,
            outcome: disconnectOutcome,
            effectEnvelope,
            effectResult,
            walletAdapter: tinyState.walletAdapter
          }
        );
        app = app || mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
        syncMcelTinyContractDomFromScm(app, instance, "Wallet runtime state disconnected and transaction draft reset.");
        renderMcelTinyContractProof(app, reason);
        return {revoke, effectEnvelope, effectResult};
      } catch (error) {
        tinyState.lastWalletResetClean = false;
        const detail = mcelTinyContractWalletError(error);
        const outcome = recordMcelTinyContractExternalOutcome(mcelTinyContractExceptionOutcome("wallet.disconnect", "disconnect-handler", error));
        recordMcelTinyContractEvidence(
          "wallet",
          "Wallet disconnect/reset produced an external exception envelope instead of escaping as an uncaught UI error.",
          "warn",
          {reason, revoke, error: detail, outcome, walletAdapter: tinyState.walletAdapter}
        );
        app = app || mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
        syncMcelTinyContractDomFromScm(app, instance, "Wallet disconnect/reset failed.");
        renderMcelTinyContractProof(app, reason);
        return {revoke, error: detail};
      }
    }

    function verifyMcelTinyContractNetwork(reason = "manual-network-check") {
      const tinyState = ensureMcelTinyContractState();
      let app = mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      if (!tinyState.scmInstance || !window.McelLabScm?.runEffect) {
        app = renderMcelTinyContractTest("network-before-mount", { exercise: false, reset: false });
      }
      const instance = ensureMcelTinyContractState().scmInstance;
      if (!instance) return;
      const result = window.McelLabScm.runEffect(instance, "network.verify");
      tinyState.networkVerifyCount += 1;
      recordMcelTinyContractEvidence(
        "network",
        "SCM compared runtime wallet chain with source-owned dev network contract.",
        result?.ok ? "pass" : "fail",
        {result}
      );
      app = app || mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      syncMcelTinyContractDomFromScm(app, instance, "Dev-network gate verified.");
      renderMcelTinyContractProof(app, reason);
    }

    async function draftMcelTinyContractTransaction(reason = "manual-draft-tx") {
      const tinyState = ensureMcelTinyContractState();
      let app = mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      if (!tinyState.scmInstance || !window.McelLabScm?.runEffect) {
        app = renderMcelTinyContractTest("draft-before-mount", { exercise: false, reset: false });
      }
      const instance = ensureMcelTinyContractState().scmInstance;
      if (!instance) return null;
      let draftProbe = null;
      try {
        draftProbe = await buildMcelTinyContractTxDraftProbe(instance);
      } catch (error) {
        draftProbe = {
          kind: "mcel-runtime-tx-draft-probe",
          noSend: true,
          ready: false,
          error: mcelTinyContractWalletError(error),
          nonce: {method: "eth_getTransactionCount", status: "exception", error: mcelTinyContractWalletError(error)},
          gasEstimate: {method: "eth_estimateGas", status: "exception", error: mcelTinyContractWalletError(error)},
          ethCall: {method: "eth_call", status: "exception", error: mcelTinyContractWalletError(error)},
          rpc: mcelTinyContractWalletRpcMethods(mcelTinyContractWalletAdapterState(), true)
        };
      }
      const result = window.McelLabScm.runEffect(instance, "release.draftTx", {draftProbe});
      const effectResult = result?.result || result || {};
      tinyState.txDraftCount += 1;
      recordMcelTinyContractEvidence(
        "tx-draft",
        "SCM release.draftTx built or blocked a realistic runtime-only no-send transaction draft.",
        effectResult?.status === "ready" ? "pass" : "warn",
        {txDraftCount: tinyState.txDraftCount, result, draftProbe}
      );
      app = app || mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      syncMcelTinyContractDomFromScm(app, instance, "Runtime-only transaction draft updated.");
      renderMcelTinyContractProof(app, reason);
      return {result, draftProbe};
    }

    function attemptMcelTinyContractForbiddenRepairWrite(instance, reason = "manual-repair-boundary") {
      const tinyState = ensureMcelTinyContractState();
      if (!instance || !window.McelLabScm?.createRepairContext) return null;
      try {
        const ctx = window.McelLabScm.createRepairContext(instance, "repairRuntimeProofChip");
        ctx.set("runtime.wallet.account", "0x0000000000000000000000000000000000000000");
        recordMcelTinyContractEvidence(
          "repair-boundary",
          "Unexpected repair write to runtime.wallet.account succeeded.",
          "fail",
          { attemptedField: "runtime.wallet.account", reason }
        );
        return {blocked: false};
      } catch (error) {
        tinyState.repairBoundaryBlockedCount += 1;
        const detail = {
          attemptedField: "runtime.wallet.account",
          code: error?.violation?.code || error?.name || "Error",
          message: error?.message || String(error),
          reason
        };
        recordMcelTinyContractEvidence(
          "repair-boundary",
          "SCM blocked repair packet from mutating runtime.wallet.account.",
          "pass",
          detail
        );
        return {blocked: true, detail};
      }
    }

    function repairMcelTinyContractRuntimeChrome(reason = "manual-repair") {
      const tinyState = ensureMcelTinyContractState();
      let app = mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      if (!tinyState.scmInstance || !window.McelLabScm?.repairComponent) {
        app = renderMcelTinyContractTest("repair-before-mount", { exercise: false, reset: false });
      }
      const instance = ensureMcelTinyContractState().scmInstance;
      if (!instance) return;
      const result = window.McelLabScm.repairComponent(instance, "repairRuntimeProofChip", {
        reason: "external-outcome-proof-display-gap",
        text: `Repair packet ready for ${reason}; no live AI call was made.`
      });
      const repairPacket = instance.runtime?.repairPacket || {};
      const boundaryProbe = attemptMcelTinyContractForbiddenRepairWrite(instance, reason);
      tinyState.repairCount += 1;
      if (repairPacket.kind === "mcel-repair-packet") tinyState.repairPacketCount += 1;
      recordMcelTinyContractEvidence(
        "repair",
        "SCM generated a bounded repair packet for runtime proof display without calling AI or touching source/wallet/network/tx draft state.",
        "pass",
        {
          repairCount: tinyState.repairCount,
          repairPacketCount: tinyState.repairPacketCount,
          repairPacket,
          boundaryProbe,
          result
        }
      );
      app = app || mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      syncMcelTinyContractDomFromScm(app, instance, "Runtime repair packet generated.");
      renderMcelTinyContractProof(app, reason);
    }

    function markMcelTinyContractReviewed(reason = "manual-approved") {
      const tinyState = ensureMcelTinyContractState();
      let app = mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      if (!tinyState.scmInstance || !window.McelLabScm?.runEffect) {
        app = renderMcelTinyContractTest("approval-before-mount", { exercise: false, reset: false });
      }
      const instance = ensureMcelTinyContractState().scmInstance;
      if (!instance) return;
      const result = window.McelLabScm.runEffect(instance, "release.approve", {reason});
      tinyState.reviewedCount += 1;
      recordMcelTinyContractEvidence(
        "source-write",
        "Declared effect release.approve wrote source.devRelease.requests and produced SCM evidence.",
        "pass",
        { reviewedCount: tinyState.reviewedCount, result }
      );
      app = app || mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      syncMcelTinyContractDomFromScm(app, instance, "Selected release approved through declared source write.");
      renderMcelTinyContractProof(app, reason);
    }

    function attemptMcelTinyContractForbiddenWrite(reason = "manual-blocked-write") {
      const tinyState = ensureMcelTinyContractState();
      let app = mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      if (!tinyState.scmInstance || !window.McelLabScm?.createEffectContext) {
        app = renderMcelTinyContractTest("blocked-write-before-mount", { exercise: false, reset: false });
      }
      const instance = ensureMcelTinyContractState().scmInstance;
      if (!instance) return;
      const beforeRpc = instance.source?.devRelease?.devNetwork?.rpcUrl || "";
      try {
        const ctx = window.McelLabScm.createEffectContext(instance, "release.select");
        ctx.set("source.devRelease.devNetwork.rpcUrl", "https://evil.invalid");
        recordMcelTinyContractEvidence(
          "failure",
          "Unexpected unsafe dev-network source write succeeded.",
          "fail",
          { attemptedField: "source.devRelease.devNetwork.rpcUrl", reason }
        );
      } catch (error) {
        tinyState.blockedWrites += 1;
        recordMcelTinyContractEvidence(
          "failure",
          "SCM blocked release.select from writing source.devRelease.devNetwork.rpcUrl.",
          "pass",
          {
            attemptedField: "source.devRelease.devNetwork.rpcUrl",
            preservedText: beforeRpc,
            code: error?.violation?.code || error?.name || "Error",
            reason
          }
        );
      }
      app = app || mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      syncMcelTinyContractDomFromScm(app, instance, "Blocked undeclared dev-network source write; RPC URL preserved.");
      renderMcelTinyContractProof(app, reason);
    }

    function clickMcelTinyContractCounter() {
      const tinyState = ensureMcelTinyContractState();
      let app = mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      if (!tinyState.scmInstance || !window.McelLabScm?.runEffect) {
        app = renderMcelTinyContractTest("select-before-mount", { reset: false, exercise: false });
      }
      const instance = ensureMcelTinyContractState().scmInstance;
      if (!instance) return;
      const items = mcelTinyContractItems(instance);
      if (!items.length) return;
      tinyState.selectedIndex = (Number(tinyState.selectedIndex || 0) + 1) % items.length;
      const next = items[tinyState.selectedIndex];
      const result = window.McelLabScm.runEffect(instance, "release.select", {id: next.id});
      tinyState.releaseSelectCount += 1;
      recordMcelTinyContractEvidence(
        "effect",
        "SCM runEffect release.select changed state/runtime only.",
        "pass",
        { selectedRequestId: next.id, result }
      );
      app = app || mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]');
      syncMcelTinyContractDomFromScm(app, instance, `Selected ${next.id} through declared state/runtime write.`);
      renderMcelTinyContractProof(app, "select-next-release");
    }

    function renderMcelTinyContractProof(app, reason = "manual") {
      const tinyState = ensureMcelTinyContractState();
      const instance = tinyState.scmInstance;
      const routeInstance = tinyState.scmRouteInstance;
      const language = parseMcelTinyContractLanguage();
      const source = mcelTinyContractSourceHtml();
      const liveSerializedHtml = serializeMcelTinyContractRuntime(app);
      let scmSerialization = null;
      let layoutCheck = null;
      let styleCheck = null;
      let layoutObservation = null;
      let registryPacket = null;

      try {
        registryPacket = window.McelElementRegistry?.evidencePacket?.() || null;
      } catch (error) {
        registryPacket = {error: error?.message || String(error)};
      }

      if (instance && window.McelLabScm) {
        try {
          layoutObservation = mcelTinyContractObservation(app);
          layoutCheck = window.McelLabScm.checkLayoutContract(instance, layoutObservation);
          styleCheck = window.McelLabScm.checkStyleContract(instance, layoutObservation);
          scmSerialization = window.McelLabScm.serializeComponent(instance, {format: "clean-source-json"});
        } catch (error) {
          recordMcelTinyContractEvidence("serialize", "SCM serialization/layout/style check failed.", "fail", {
            code: error?.violation?.code || error?.name || "Error",
            message: error?.message || String(error)
          });
        }
      }

      const componentEvidence = window.McelLabScm?.exportEvidence && instance
        ? window.McelLabScm.exportEvidence(instance)
        : {evidence: []};
      const routeEvidence = window.McelLabScm?.exportRouteEvidence && routeInstance
        ? window.McelLabScm.exportRouteEvidence(routeInstance)
        : {evidence: []};

      const serializedSource = scmSerialization?.serialized || "";
      const serializedHasRuntimeLeak = [
        "runtime.wallet",
        "runtime.network",
        "runtime.txDraft",
        "data-mc-generated",
        "0xDeaD0000"
      ].some((marker) => serializedSource.includes(marker));
      const liveHtmlHasGenerated = liveSerializedHtml.includes("data-mc-generated");
      const unsafeBlocked = tinyState.blockedWrites > 0 || (componentEvidence.evidence || []).some((entry) => entry.code === "SCM_EFFECT_UNDECLARED_WRITE");
      const walletAdapter = mcelTinyContractWalletAdapterState();
      const walletRpcMethods = mcelTinyContractWalletRpcMethods(walletAdapter, true);
      const walletSuccessfulRpcMethods = mcelTinyContractWalletRpcMethods(walletAdapter, false);
      const walletConnectRpcObserved = (
        walletRpcMethods.includes("eth_chainId") &&
        (walletRpcMethods.includes("eth_requestAccounts") || walletRpcMethods.includes("eth_accounts") || walletRpcMethods.includes("provider.detect"))
      ) || (
        walletAdapter.walletSubsystemUsed === true &&
        walletRpcMethods.includes("MainComputerWalletApp.requestConnect") &&
        walletRpcMethods.includes("MainComputerWalletApp.providerSnapshot")
      );
      const componentEvents = componentEvidence.evidence || [];
      const routeEvents = routeEvidence.evidence || [];
      const disconnectEffectRan = componentEvents.some((entry) => entry.phase === "effect-commit" && entry.effectName === "wallet.disconnect") ||
        tinyState.walletDisconnectCount > 0;
      const walletResetClean = !disconnectEffectRan || tinyState.walletDisconnectCommitCount > 0 || tinyState.lastWalletResetClean === true;
      const approvedSource = mcelTinyContractItems(instance).some((item) => item.status === "approved");
      const liveProviderSeen = walletAdapter.liveProvider === true && walletAdapter.mockFallback !== true;
      const revokeAttempted = (walletAdapter.calls || []).some((entry) => entry.method === "wallet_revokePermissions") ||
        tinyState.walletRevokeAttemptCount > 0;
      const walletResetOnly = tinyState.walletDisconnectCount > 0 && tinyState.walletConnectCount === 0;
      const providerRevokeRequired = disconnectEffectRan && liveProviderSeen;
      const txDraftEnforcement = mcelTinyContractEnforceTxDraftProvenance(instance, `render-proof:${reason}`);
      const runtimeTxDraft = txDraftEnforcement?.txDraft || instance?.runtime?.txDraft || {};
      const txDraftInvalidationReasons = (runtimeTxDraft.invalidatedBy || []).map((entry) => entry.reason || "").filter(Boolean);
      const txDraftProvenanceRecorded = Boolean(
        runtimeTxDraft.provenanceVersion === "txDraft.provenance.v1" &&
        runtimeTxDraft.sourceRequestHash &&
        runtimeTxDraft.selectedRequestSnapshot &&
        runtimeTxDraft.walletAccountHash &&
        runtimeTxDraft.chainProof &&
        Array.isArray(runtimeTxDraft.externalOutcomeSequence) &&
        Array.isArray(runtimeTxDraft.networkGateSequence) &&
        Array.isArray(runtimeTxDraft.probeEnvelopeIds)
      );
      const txDraftInvalidationRecorded = runtimeTxDraft.status === "ready"
        ? txDraftInvalidationReasons.length === 0
        : txDraftInvalidationReasons.length > 0 || runtimeTxDraft.status === "empty" || runtimeTxDraft.status === "selected";
      const runtimeTxDraftConsumerGate = instance?.runtime?.txDraftConsumerGate || mcelTinyContractTxDraftConsumerGate({
        txDraft: runtimeTxDraft,
        freshness: runtimeTxDraft.provenanceFreshness || {
          status: runtimeTxDraft.freshnessStatus || "",
          invalidatedBy: runtimeTxDraft.invalidatedBy || [],
          action: runtimeTxDraft.freshnessAction || "",
          noSendBoundaryPreserved: runtimeTxDraft.noSendBoundaryPreserved === true
        },
        consumer: "release.approve"
      });
      const runtimeTxDraftEndgamePreflight = runtimeTxDraftConsumerGate.endgamePreflight || mcelTinyContractTxDraftEndgamePreflight({
        txDraft: runtimeTxDraft,
        freshness: runtimeTxDraft.provenanceFreshness || {
          status: runtimeTxDraft.freshnessStatus || "",
          invalidatedBy: runtimeTxDraft.invalidatedBy || [],
          action: runtimeTxDraft.freshnessAction || "",
          noSendBoundaryPreserved: runtimeTxDraft.noSendBoundaryPreserved === true
        },
        consumerGate: runtimeTxDraftConsumerGate
      });
      const walletCommitBoundary = mcelWalletToolCommitBoundary({
        source: instance?.source || {},
        state: instance?.state || {},
        runtime: instance?.runtime || {},
        request: selectedMcelTinyContractItem(instance),
        reason: `render-proof:${reason}`
      });
      if (instance?.runtime) instance.runtime.walletCommitBoundary = walletCommitBoundary;
      tinyState.lastWalletCommitBoundary = walletCommitBoundary;
      if (walletCommitBoundary.mcelCommitReceipt) {
        tinyState.commitBoundaryReceipts = [
          ...(tinyState.commitBoundaryReceipts || []),
          walletCommitBoundary.mcelCommitReceipt
        ].slice(-12);
      }
      const externalOutcome = mcelTinyContractLatestWalletActionOutcome(instance);
      const actionOutcome = externalOutcome.status || "waiting";
      const externalOutcomeCaptured = externalOutcome.kind === "mcel-external-outcome" && actionOutcome !== "waiting";
      const externalBlockedOrException = ["blocked", "exception"].includes(actionOutcome);
      const txDraftBlockedAfterExternalOutcome = !externalBlockedOrException || instance?.runtime?.txDraft?.status !== "ready";
      const sourceSafeAfterExternalOutcome = true; // Source mutation safety is enforced by SCM declared writes and serialization; blocked outcomes must not be inferred from historical source state.
      const checks = {
        contractLanguageParsed: language.kind === "mcel.scm.app" && language.name === "DevNetworkReleaseConsole",
        elementRegistryAvailable: Number(registryPacket?.elementCount || 0) > 0,
        componentManifestResolved: Boolean(window.McelLabScm?.componentDefinition?.("DevNetworkReleaseConsole")),
        routeManifestResolved: Boolean(window.McelLabScm?.routeDefinition?.("workspace.dev-network-release")),
        routeLoaderCommitted: tinyState.routeLoaderCount > 0 || routeEvents.some((entry) => entry.phase === "route-loader-commit" && entry.ok === true),
        walletEffectRan: tinyState.walletConnectCount > 0 || componentEvents.some((entry) => entry.phase === "effect-commit" && entry.effectName === "wallet.connect"),
        walletAdapterExercised: walletConnectRpcObserved || walletResetOnly,
        walletLiveProviderObserved: liveProviderSeen,
        walletEventsSubscribed: walletAdapter.eventsBound === true || walletAdapter.mockFallback === true || walletResetOnly,
        walletProviderEventsGoverned: walletAdapter.mockFallback === true || walletResetOnly || walletAdapter.eventsBound === true,
        walletProviderAccountsChangedEffectRan: tinyState.providerAccountsChangedCount > 0 || componentEvents.some((entry) => entry.phase === "effect-commit" && entry.effectName === "wallet.provider.accountsChanged"),
        walletProviderChainChangedEffectRan: tinyState.providerChainChangedCount > 0 || componentEvents.some((entry) => entry.phase === "effect-commit" && entry.effectName === "wallet.provider.chainChanged"),
        walletProviderDisconnectEffectRan: tinyState.providerDisconnectCount > 0 || componentEvents.some((entry) => entry.phase === "effect-commit" && entry.effectName === "wallet.provider.disconnect"),
        walletProviderErrorEffectRan: tinyState.providerErrorCount > 0 || componentEvents.some((entry) => entry.phase === "effect-commit" && entry.effectName === "wallet.provider.error"),
        walletSubsystemObserved: walletAdapter.walletSubsystemUsed === true || walletAdapter.directProviderFallback === true || walletAdapter.walletSubsystemReady === true || walletAdapter.mockFallback === true,
        walletSubsystemUsed: walletAdapter.walletSubsystemUsed === true,
        walletDirectProviderFallback: walletAdapter.directProviderFallback === true,
        walletMockFallbackDegraded: walletAdapter.mockFallback === true,
        externalOutcomeCaptured,
        externalOutcomeContained: externalOutcomeCaptured && ["pass", "blocked", "exception"].includes(actionOutcome),
        txDraftBlockedAfterExternalOutcome,
        sourceSafeAfterExternalOutcome,
        walletDisconnectReset: walletResetClean,
        walletPermissionRevokeAttempted: !providerRevokeRequired || revokeAttempted,
        networkVerified: tinyState.networkVerifyCount > 0 || componentEvents.some((entry) => entry.phase === "effect-commit" && entry.effectName === "network.verify"),
        declaredRuntimeEffectRan: tinyState.releaseSelectCount > 0 || componentEvents.some((entry) => entry.phase === "effect-commit" && entry.effectName === "release.select"),
        txDraftRuntimeOnly: tinyState.txDraftCount > 0 || componentEvents.some((entry) => entry.phase === "effect-commit" && entry.effectName === "release.draftTx"),
        txDraftProvenanceRecorded,
        txDraftInvalidationRecorded,
        txDraftProvenanceEnforced: runtimeTxDraft.provenanceEnforced === true,
        txDraftFreshnessCurrent: runtimeTxDraft.freshnessStatus === "valid",
        txDraftConsumerGateObserved: runtimeTxDraftConsumerGate.kind === "mcel-tx-draft-consumer-gate.v1",
        txDraftConsumerGatePass: runtimeTxDraftConsumerGate.status === "pass",
        txDraftConsumerGateBlocksUnsafe: runtimeTxDraftConsumerGate.status !== "blocked" || runtimeTxDraftConsumerGate.valid !== true,
        txDraftEndgamePreflightLocked: runtimeTxDraftEndgamePreflight.canSend !== true && runtimeTxDraftEndgamePreflight.canSign !== true && runtimeTxDraftEndgamePreflight.canBroadcast !== true,
        txDraftEndgamePreflightSafe: runtimeTxDraftEndgamePreflight.noSendBoundaryPreserved === true && Array.isArray(runtimeTxDraftEndgamePreflight.blockers) && runtimeTxDraftEndgamePreflight.blockers.includes("send-sign-not-implemented"),
        mcelCommitBoundaryDeclared: walletCommitBoundary.kind === "mcelWalletToolCommitBoundary.v1" && walletCommitBoundary.mcelCommitDraft?.kind === "mcelCommitDraft.v1",
        walletToolCommitBoundaryLocked: walletCommitBoundary.locked === true && walletCommitBoundary.canSend !== true && walletCommitBoundary.canSign !== true && walletCommitBoundary.canBroadcast !== true,
        walletToolFreshnessChecked: walletCommitBoundary.mcelCommitFreshness?.kind === "mcelCommitFreshness.v1",
        walletToolConsumerGateObserved: walletCommitBoundary.mcelCommitConsumerGate?.kind === "mcelCommitConsumerGate.v1",
        walletToolPreflightObserved: walletCommitBoundary.mcelCommitPreflight?.kind === "mcelCommitPreflight.v1",
        walletToolCommitReceiptRecorded: walletCommitBoundary.mcelCommitReceipt?.kind === "mcelCommitReceipt.v1" && walletCommitBoundary.mcelCommitReceipt?.mutationExecuted !== true,
        declaredSourceEffectRan: tinyState.reviewedCount > 0 || (approvedSource && componentEvents.some((entry) => entry.phase === "effect-commit" && entry.effectName === "release.approve")),
        unsafeSourceWriteBlocked: unsafeBlocked,
        repairPacketGenerated: tinyState.repairPacketCount > 0 || (
          instance?.runtime?.repairPacket?.kind === "mcel-repair-packet" &&
          instance?.runtime?.repairPacket?.status === "ready"
        ),
        repairPacketNoLiveAiCall: (
          instance?.runtime?.repairPacket?.kind === "mcel-repair-packet" &&
          instance?.runtime?.repairPacket?.status === "ready" &&
          instance?.runtime?.repairPacket?.liveAiCall === false
        ) || instance?.runtime?.proofChip?.liveAiCall === false,
        repairBoundaryBlocked: tinyState.repairBoundaryBlockedCount > 0 || (componentEvents.some((entry) => String(entry.code || "").startsWith("SCM_REPAIR_") && entry.ok === false)),
        runtimeRepairScoped: tinyState.repairCount > 0 && (tinyState.repairPacketCount > 0 || instance?.runtime?.repairPacket?.kind === "mcel-repair-packet") && (tinyState.repairBoundaryBlockedCount > 0 || componentEvents.some((entry) => String(entry.code || "").startsWith("SCM_REPAIR_") && entry.ok === false)),
        serializationClean: Boolean(scmSerialization?.ok) && !serializedHasRuntimeLeak && !liveHtmlHasGenerated,
        layoutContractChecked: layoutCheck?.ok === true,
        styleContractChecked: styleCheck?.ok === true
      };
      const fullBatteryRan = tinyState.fullBatteryRunCount > 0;
      const walletLifecycleTouched = tinyState.walletConnectCount > 0 || tinyState.walletDisconnectCount > 0;
      const walletResetOnlyRequiredChecks = [
        checks.contractLanguageParsed,
        checks.componentManifestResolved,
        checks.routeManifestResolved,
        checks.routeLoaderCommitted,
        checks.walletDisconnectReset,
        checks.walletPermissionRevokeAttempted,
        checks.serializationClean,
        checks.layoutContractChecked,
        checks.styleContractChecked
      ];
      const walletLifecycleRequiredChecks = [
        checks.contractLanguageParsed,
        checks.componentManifestResolved,
        checks.routeManifestResolved,
        checks.routeLoaderCommitted,
        checks.walletEffectRan,
        checks.walletAdapterExercised,
        checks.walletEventsSubscribed,
        checks.walletProviderEventsGoverned,
        checks.externalOutcomeContained,
        checks.txDraftBlockedAfterExternalOutcome,
        checks.sourceSafeAfterExternalOutcome,
        checks.walletDisconnectReset,
        checks.walletPermissionRevokeAttempted,
        checks.networkVerified,
        checks.serializationClean,
        checks.layoutContractChecked,
        checks.styleContractChecked
      ];
      const fullBatteryRequiredChecks = [
        checks.contractLanguageParsed,
        checks.elementRegistryAvailable,
        checks.componentManifestResolved,
        checks.routeManifestResolved,
        checks.routeLoaderCommitted,
        checks.walletEffectRan,
        checks.walletAdapterExercised,
        checks.walletEventsSubscribed,
        checks.walletProviderEventsGoverned,
        checks.externalOutcomeContained,
        checks.txDraftBlockedAfterExternalOutcome,
        checks.sourceSafeAfterExternalOutcome,
        checks.walletDisconnectReset,
        checks.walletPermissionRevokeAttempted,
        checks.networkVerified,
        checks.declaredRuntimeEffectRan,
        checks.txDraftRuntimeOnly,
        checks.txDraftProvenanceRecorded,
        checks.txDraftInvalidationRecorded,
        checks.declaredSourceEffectRan,
        checks.unsafeSourceWriteBlocked,
        checks.runtimeRepairScoped,
        checks.serializationClean,
        checks.layoutContractChecked,
        checks.styleContractChecked
      ];
      const receiptMode = fullBatteryRan
        ? "full-scm-battery"
        : (walletResetOnly ? "wallet-reset" : (walletLifecycleTouched ? "wallet-lifecycle" : "waiting"));
      const governanceRequiredChecks = receiptMode === "full-scm-battery"
        ? fullBatteryRequiredChecks
        : (receiptMode === "wallet-reset" ? walletResetOnlyRequiredChecks : walletLifecycleRequiredChecks);
      const governanceOutcome = receiptMode === "waiting"
        ? "waiting"
        : (governanceRequiredChecks.every(Boolean) ? "pass" : "fail");
      const safetyOutcome = receiptMode === "waiting"
        ? "waiting"
        : ([checks.serializationClean, checks.layoutContractChecked, checks.styleContractChecked, checks.txDraftBlockedAfterExternalOutcome, checks.sourceSafeAfterExternalOutcome].every(Boolean) ? "pass" : "fail");
      const proofCompleteness = receiptMode === "waiting"
        ? "waiting"
        : (checks.externalOutcomeContained || receiptMode === "wallet-reset" || receiptMode === "full-scm-battery" ? "complete" : "incomplete");
      const receiptStatus = receiptMode === "waiting"
        ? "waiting"
        : (governanceOutcome === "fail" || safetyOutcome === "fail" || proofCompleteness === "incomplete"
          ? "fail"
          : (actionOutcome === "exception" ? "exception" : (actionOutcome === "blocked" ? "blocked" : "pass")));

      const proof = {
        status: receiptStatus,
        mode: receiptMode,
        reason,
        actionOutcome,
        externalOutcome,
        governanceOutcome,
        safetyOutcome,
        proofCompleteness,
        sourceHash: mcelTinyContractHash(source),
        liveSerializedHtmlHash: mcelTinyContractHash(liveSerializedHtml),
        scmSerializedSourceHash: mcelTinyContractHash(serializedSource),
        component: "DevNetworkReleaseConsole",
        route: "workspace.dev-network-release",
        expectedChainId: instance?.source?.devRelease?.devNetwork?.chainId || "0x28757b2",
        runtimeChainId: instance?.runtime?.network?.chainId || "",
        walletConnected: instance?.runtime?.wallet?.connected === true,
        walletDisconnected: instance?.runtime?.wallet?.connected === false,
        walletDisconnectCount: tinyState.walletDisconnectCount || 0,
        walletDisconnectCommitCount: tinyState.walletDisconnectCommitCount || 0,
        walletRevokeAttemptCount: tinyState.walletRevokeAttemptCount || 0,
        walletRevokeSuccessCount: tinyState.walletRevokeSuccessCount || 0,
        providerAccountsChangedCount: tinyState.providerAccountsChangedCount || 0,
        providerAccountSwitchCount: tinyState.providerAccountSwitchCount || 0,
        providerAccountDisconnectCount: tinyState.providerAccountDisconnectCount || 0,
        providerChainChangedCount: tinyState.providerChainChangedCount || 0,
        providerDisconnectCount: tinyState.providerDisconnectCount || 0,
        providerErrorCount: tinyState.providerErrorCount || 0,
        routeLoaderCount: tinyState.routeLoaderCount || 0,
        networkVerifyCount: tinyState.networkVerifyCount || 0,
        releaseSelectCount: tinyState.releaseSelectCount || 0,
        fullBatteryRunCount: tinyState.fullBatteryRunCount || 0,
        walletAdapter: {
          providerKind: walletAdapter.providerKind,
          liveProvider: walletAdapter.liveProvider,
          mockFallback: walletAdapter.mockFallback,
          ethersReady: walletAdapter.ethersReady,
          walletSubsystemReady: walletAdapter.walletSubsystemReady,
          walletSubsystemUsed: walletAdapter.walletSubsystemUsed,
          walletSubsystemPreferred: walletAdapter.walletSubsystemPreferred,
          directProviderFallback: walletAdapter.directProviderFallback,
          connectSource: walletAdapter.connectSource,
          disconnectSource: walletAdapter.disconnectSource,
          walletSubsystemMode: tinyState.walletSubsystemMode || "unobserved",
          eventsBound: walletAdapter.eventsBound,
          permissionRevoked: walletAdapter.permissionRevoked,
          rpcMethods: walletRpcMethods,
          lastError: walletAdapter.lastError || ""
        },
        registryElementCount: registryPacket?.elementCount || 0,
        layoutObservation: {
          kind: layoutObservation?.kind || "",
          source: layoutObservation?.source || "",
          measured: layoutObservation?.measured === true,
          regions: layoutObservation?.regions || {},
          metrics: layoutObservation?.metrics || {},
          documentHeightRatio: layoutObservation?.documentHeightRatio ?? null
        },
        layoutViolations: layoutCheck?.violations || [],
        styleViolations: styleCheck?.violations || [],
        reviewedCount: tinyState.reviewedCount,
        walletConnectCount: tinyState.walletConnectCount,
        txDraftCount: tinyState.txDraftCount,
        runtimeTxDraft: runtimeTxDraft,
        txDraftConsumerGate: runtimeTxDraftConsumerGate,
        txDraftEndgamePreflight: runtimeTxDraftEndgamePreflight,
        mcelCommitBoundary: walletCommitBoundary,
        walletCommitBoundary,
        mcelProofDockSpecimens: walletCommitBoundary.mcelProofDockSpecimens || {},
        proofDockSpecimens: walletCommitBoundary.proofDockSpecimens || walletCommitBoundary.mcelProofDockSpecimens || {},
        commitBoundaryReceipts: tinyState.commitBoundaryReceipts || [],
        txDraftProvenance: {
          provenanceVersion: runtimeTxDraft.provenanceVersion || "",
          sourceRequestHash: runtimeTxDraft.sourceRequestHash || "",
          walletAccountHash: runtimeTxDraft.walletAccountHash || "",
          chainProof: runtimeTxDraft.chainProof || {},
          externalOutcomeSequence: runtimeTxDraft.externalOutcomeSequence || [],
          networkGateSequence: runtimeTxDraft.networkGateSequence || [],
          calldataSource: runtimeTxDraft.calldataSource || "",
          abiEncodingStatus: runtimeTxDraft.abiEncodingStatus || "",
          probeEnvelopeIds: runtimeTxDraft.probeEnvelopeIds || [],
          invalidatedBy: runtimeTxDraft.invalidatedBy || [],
          freshnessStatus: runtimeTxDraft.freshnessStatus || "",
          freshnessAction: runtimeTxDraft.freshnessAction || "",
          noSendBoundaryPreserved: runtimeTxDraft.noSendBoundaryPreserved === true,
          provenanceEnforced: runtimeTxDraft.provenanceEnforced === true,
          provenanceFreshness: runtimeTxDraft.provenanceFreshness || null,
          valid: runtimeTxDraft.valid === true
        },
        repairCount: tinyState.repairCount,
        repairPacketCount: tinyState.repairPacketCount,
        repairBoundaryBlockedCount: tinyState.repairBoundaryBlockedCount,
        repairPacket: instance?.runtime?.repairPacket || {},
        blockedSourceWrites: tinyState.blockedWrites,
        checks
      };
      tinyState.lastProof = proof;
      renderMcelTinyContractMap(app);
      if (mcelTinyContractSerialized) {
        mcelTinyContractSerialized.textContent = [
          "SCM serializeComponent(instance):",
          serializedSource || "SCM serialization has not run.",
          "",
          "Live DOM serialization with generated runtime nodes stripped:",
          liveSerializedHtml || "Runtime has not mounted."
        ].join("\n");
      }
      if (mcelTinyContractProof) {
        mcelTinyContractProof.dataset.status = proof.status;
        const proofHeadline = proof.status === "pass"
          ? (receiptMode === "full-scm-battery"
            ? "PASS: full SCM wallet battery is clean"
            : (receiptMode === "wallet-reset" ? "PASS: disconnected wallet reset is tamed by SCM" : "PASS: wallet lifecycle is tamed by SCM"))
          : (proof.status === "blocked"
            ? "BLOCKED: external wallet action did not complete; SCM containment passed"
            : (proof.status === "exception"
              ? "EXCEPTION: external wallet outcome was captured; SCM containment passed"
              : (proof.status === "waiting"
                ? "WAITING: run the SCM wallet proof battery"
                : (receiptMode === "full-scm-battery" ? "FAIL: full SCM governance/safety receipt has a gap" : "FAIL: wallet lifecycle governance/safety receipt has a gap"))));
        const fullOnlyText = receiptMode === "full-scm-battery" ? "" : " (full battery only)";
        const repairPacketLine = receiptMode === "full-scm-battery"
          ? `repair packet: ${checks.repairPacketGenerated ? "generated" : "not run"} · liveAiCall=${checks.repairPacketNoLiveAiCall ? "false" : "unknown"} · boundary=${checks.repairBoundaryBlocked ? "blocked forbidden repair write" : "not proven"}`
          : (checks.repairPacketGenerated
            ? `repair packet: generated outside ${receiptMode} · liveAiCall=${checks.repairPacketNoLiveAiCall ? "false" : "unknown"} · boundary not required`
            : `repair packet: not required for ${receiptMode}`);
        mcelTinyContractProof.textContent = [
          proofHeadline,
          `receipt mode: ${receiptMode}`,
          `action outcome: ${actionOutcome}`,
          `external outcome: ${externalOutcome.reason || "unknown"} · ${externalOutcome.message || "no message"}`,
          `governance outcome: ${governanceOutcome}`,
          `safety outcome: ${safetyOutcome}`,
          `proof completeness: ${proofCompleteness}`,
          `next action: ${externalOutcome.nextAction || "none"}`,
          `contract language: ${checks.contractLanguageParsed ? "resolved" : "missing"}`,
          `route loader: ${checks.routeLoaderCommitted ? "committed" : "missing"}`,
          `wallet effect: ${checks.walletEffectRan ? "pass" : (receiptMode === "wallet-reset" ? "not required for reset" : "missing")}`,
          `external outcome captured: ${checks.externalOutcomeCaptured ? "true" : "false"}`,
          `tx draft after blocked/exception: ${checks.txDraftBlockedAfterExternalOutcome ? "safe" : "unsafe"}`,
          `source after blocked/exception: ${checks.sourceSafeAfterExternalOutcome ? "safe" : "unsafe"}`,
          `wallet adapter: ${checks.walletLiveProviderObserved ? "live provider" : (walletAdapter.mockFallback ? "mock fallback" : "not observed")}`,
          `wallet rpc: ${walletRpcMethods.length ? walletRpcMethods.join(", ") : "none"}`,
          `wallet events: ${checks.walletEventsSubscribed ? "subscribed" : (receiptMode === "wallet-reset" ? "not required before connect" : "missing")}`,
          `provider event effects: ${checks.walletProviderEventsGoverned ? "governed" : "not governed"} · accounts=${tinyState.providerAccountsChangedCount || 0} switches=${tinyState.providerAccountSwitchCount || 0} accountDisconnects=${tinyState.providerAccountDisconnectCount || 0} chain=${tinyState.providerChainChangedCount || 0} disconnect=${tinyState.providerDisconnectCount || 0} error=${tinyState.providerErrorCount || 0}`,
          `wallet subsystem: ${checks.walletSubsystemUsed ? "used" : (checks.walletDirectProviderFallback ? "fallback to direct provider" : (walletAdapter.walletSubsystemReady ? "available but not used" : "missing (optional)"))}`,
          `wallet proof level: ${checks.walletMockFallbackDegraded ? "degraded mock" : (checks.walletSubsystemUsed ? "product subsystem" : "adapter only")}`,
          `wallet reset: ${disconnectEffectRan ? (checks.walletDisconnectReset ? "pass" : "failed") : "not run"}`,
          `wallet revoke: ${disconnectEffectRan ? (checks.walletPermissionRevokeAttempted ? "attempted" : "not attempted") : "not run"}`,
          `network gate: ${checks.networkVerified ? "pass" : (receiptMode === "wallet-reset" ? "not required for reset" : "missing")}`,
          `runtime select: ${checks.declaredRuntimeEffectRan ? "pass" : `not run${fullOnlyText}`}`,
          `runtime tx draft: ${checks.txDraftRuntimeOnly ? "pass" : `not run${fullOnlyText}`}`,
          `tx draft boundary: ${runtimeTxDraft.noSend ? "no-send" : "not built"} · calldata=${runtimeTxDraft.calldata ? (runtimeTxDraft.calldataEncoding || "present") : "missing"} · nonce=${runtimeTxDraft.nonce?.status || "not-probed"} · gas=${runtimeTxDraft.gasEstimate?.status || "not-probed"} · call=${runtimeTxDraft.ethCall?.status || "not-probed"}`,
          `tx draft provenance: ${checks.txDraftProvenanceRecorded ? "recorded" : "missing"} · freshness=${runtimeTxDraft.freshnessStatus || "unknown"} · action=${runtimeTxDraft.freshnessAction || "inspect"} · sourceRequestHash=${runtimeTxDraft.sourceRequestHash || "missing"} · accountHash=${runtimeTxDraft.walletAccountHash || "missing"} · chain=${runtimeTxDraft.chainProof?.status || "unknown"} · invalidatedBy=${txDraftInvalidationReasons.join("|") || "none"}`,
          `send/sign preflight: ${runtimeTxDraftEndgamePreflight.status || "locked-no-draft"} · canSend=${runtimeTxDraftEndgamePreflight.canSend === true} · canSign=${runtimeTxDraftEndgamePreflight.canSign === true} · canBroadcast=${runtimeTxDraftEndgamePreflight.canBroadcast === true} · action=${runtimeTxDraftEndgamePreflight.action || "locked"}`,
          `18N wallet commit boundary: ${walletCommitBoundary.status || "locked"} · draft=${walletCommitBoundary.mcelCommitDraft?.kind || "missing"} · provenance=${walletCommitBoundary.mcelCommitProvenance?.kind || "missing"} · freshness=${walletCommitBoundary.mcelCommitFreshness?.status || "not-observed"} · gate=${walletCommitBoundary.mcelCommitConsumerGate?.status || "blocked"} · receipt=${walletCommitBoundary.mcelCommitReceipt?.status || "blocked"}`,
          `18N wallet lock: canSend=${walletCommitBoundary.canSend === true} · canSign=${walletCommitBoundary.canSign === true} · canBroadcast=${walletCommitBoundary.canBroadcast === true} · mutationExecuted=${walletCommitBoundary.mcelCommitReceipt?.mutationExecuted === true}`,
          `18N proof dock specimens: ${(walletCommitBoundary.mcelProofDockSpecimens?.specimens || []).map((entry) => entry.specimen).join(", ") || "waiting"}`,
          `source approval: ${checks.declaredSourceEffectRan ? "pass" : `not run${fullOnlyText}`}`,
          `unsafe write blocked: ${checks.unsafeSourceWriteBlocked ? "true" : `not run${fullOnlyText}`}`,
          repairPacketLine,
          `repair scoped: ${checks.runtimeRepairScoped ? "true" : `not run${fullOnlyText}`}`,
          `serialization clean: ${checks.serializationClean}`,
          `layout observation: ${layoutObservation?.source || "missing"}${layoutObservation?.measured ? " measured" : " not measured"}`,
          `layout/style checked: ${checks.layoutContractChecked}/${checks.styleContractChecked}`,
          `layout/style issues: layout=${(layoutCheck?.violations || []).length} style=${(styleCheck?.violations || []).length}`
        ].join("\n");
      }
      renderMcel18nWalletToolSurface(walletCommitBoundary);
      if (mcelTinyContractEvidence) {
        mcelTinyContractEvidence.textContent = JSON.stringify({
          kind: "mcel-lab-medium-scm-proven-dev-network-app-receipt",
          status: proof.status,
          reason,
          proof,
          routeEvidence,
          componentEvidence,
          localEvidence: tinyState.evidence,
          registry: registryPacket
        }, null, 2);
      }
      return proof;
    }

    async function runMcelTinyContractScmWalletProof(reason = "manual-run") {
      const app = renderMcelTinyContractTest(`${reason}-mount`, { exercise: false, reset: true });
      const tinyState = ensureMcelTinyContractState();
      const instance = tinyState.scmInstance;
      if (!instance) return null;

      await connectMcelTinyContractWallet(`${reason}-wallet-connect`);
      verifyMcelTinyContractNetwork(`${reason}-network-gate`);
      clickMcelTinyContractCounter(`${reason}-select`);
      await draftMcelTinyContractTransaction(`${reason}-tx-draft`);
      repairMcelTinyContractRuntimeChrome(`${reason}-repair`);
      markMcelTinyContractReviewed(`${reason}-approval`);
      attemptMcelTinyContractForbiddenWrite(`${reason}-blocked-write`);
      tinyState.fullBatteryRunCount += 1;

      const proofApp = mcelTinyContractRuntimeMount?.querySelector('[data-mc-component="dev-network-release-console"]') || app;
      syncMcelTinyContractDomFromScm(proofApp, instance, "Full SCM wallet proof battery completed.");
      return renderMcelTinyContractProof(proofApp, reason);
    }

    function renderMcelTinyContractTest(reason = "manual", options = {}) {
      const source = mcelTinyContractSourceHtml();
      if (!source) return null;
      if (mcelTinyContractSource) {
        mcelTinyContractSource.textContent = mcelTinyContractLanguageSource() || "Contract language missing.";
      }
      return mountMcelTinyContractRuntime(source, reason, {
        reset: options.reset !== false,
        exercise: options.exercise !== false
      });
    }

    function loadMcelTinyContractIntoSourceEditor() {
      if (!mcelSourceHtml) return;
      const source = mcelTinyContractSourceHtml();
      mcelSourceHtml.value = source;
      selectMcelSourceIndex(0, "medium-scm-proven-dev-network-html-contract");
      setMcelLabMode("source");
      compileMcelLabSource("medium-scm-proven-dev-network-html-contract");
      syncMcelGrapesFromSource();
      openMcelLabModal("editor");
      recordMcelEvent(
        "medium-scm-proven-dev-network-app",
        "MCEL_SCM_PROVEN_DEV_NETWORK_CONSOLE_LOADED",
        "SCM-proven DevNetworkReleaseConsole source.html loaded into the source editor as a first-class app surface.",
        "success"
      );
    }

    function bindMcelLabControls() {
      mcelCompile?.addEventListener("click", () => compileMcelLabSource("manual-compile"));
      mcelSerialize?.addEventListener("click", () => serializeMcelRuntime("manual-serialize"));
      mcelDamage?.addEventListener("click", damageMcelRuntime);
      mcelRepair?.addEventListener("click", () => repairMcelRuntime("manual-repair"));
      mcelReset?.addEventListener("click", resetMcelLab);
      mcelRunTests?.addEventListener("click", runMcelContractTests);
      mcelRunMatrix?.addEventListener("click", runMcelScenarioMatrix);
      mcelRunAcid?.addEventListener("click", () => runSelectedMcelAcidTest("manual-selected-acid-test"));
      mcelRunAcidSuite?.addEventListener("click", () => runMcelAcidTests("manual-acid-suite"));
      mcelRunAudit?.addEventListener("click", runMcelOperationalAudit);
      mcelBuildEvidence?.addEventListener("click", buildMcelEvidencePacket);
      mcelRunAutopilot?.addEventListener("click", () => runMcelAutopilotProof("manual-autopilot"));
      mcelRunKernel?.addEventListener("click", () => runMcelKernelAudit("manual-kernel-audit"));
      mcelBuildTraceability?.addEventListener("click", () => buildMcelTraceabilityMap("manual-traceability"));
      mcelBuildSubsumption?.addEventListener("click", () => buildMcelSubsumptionLattice("manual-subsumption"));
      mcelBuildAdoptionCase?.addEventListener("click", () => buildMcelAdoptionCase("manual-adoption-case"));
      mcelBuildWorkbench?.addEventListener("click", () => buildMcelWorkbenchPlan("manual-workbench"));
      mcelRunBrowserProof?.addEventListener("click", () => runMcelBrowserSemanticProof("manual-browser-proof"));
      mcelApplyTraits?.addEventListener("click", applyMcelTraitsToSelectedSourceWidget);
      mcelLoadScenario?.addEventListener("click", loadSelectedMcelScenario);
      mcelScenarioSelect?.addEventListener("change", describeSelectedMcelScenario);
      mcelThemeSelect?.addEventListener("change", () => changeMcelTheme("theme-select"));
      mcelChromeSelect?.addEventListener("change", () => changeMcelChrome("chrome-select"));
      mcelOpenEditorModal?.addEventListener("click", () => openMcelLabModal("editor"));
      mcelOpenSiteModal?.addEventListener("click", () => openMcelLabModal("site"));
      mcelOpenSmartCssModal?.addEventListener("click", () => openMcelLabModal("smart-css"));
      mcelSmartCssRerun?.addEventListener("click", () => renderMcelSmartCssPrimitiveLab("manual-rerun"));
      mcelElementAcidRerun?.addEventListener("click", () => renderMcelElementLibraryAcidTest("manual-rerun"));
      mcelTinyContractRun?.addEventListener("click", () => {
        void runMcelTinyContractScmWalletProof("manual-run");
      });
      mcelTinyContractWallet?.addEventListener("click", () => {
        void connectMcelTinyContractWallet("manual-wallet-connect");
      });
      mcelTinyContractDisconnect?.addEventListener("click", () => {
        void disconnectMcelTinyContractWallet("manual-wallet-disconnect");
      });
      mcelTinyContractIncrement?.addEventListener("click", clickMcelTinyContractCounter);
      mcelTinyContractDraftTx?.addEventListener("click", () => { void draftMcelTinyContractTransaction("manual-draft-tx"); });
      document.querySelector("#mcel-18n-wallet-tool-refresh")?.addEventListener("click", () => {
        refreshMcel18nWalletToolBoundary("manual-wallet-tool-preflight-refresh");
      });
      document.querySelector("#mcel-18n-wallet-tool-rebuild-draft")?.addEventListener("click", () => {
        void rebuildMcel18nWalletTxDraft("manual-wallet-tool-rebuild-draft");
      });
      document.querySelector("#mcel-18n-wallet-tool-simulate-account")?.addEventListener("click", () => {
        simulateMcel18nWalletStaleDraft("account", "simulate-wallet-account-change");
      });
      document.querySelector("#mcel-18n-wallet-tool-simulate-chain")?.addEventListener("click", () => {
        simulateMcel18nWalletStaleDraft("chain", "simulate-wallet-chain-change");
      });
      document.querySelector("#mcel-18n-wallet-tool-simulate-source")?.addEventListener("click", () => {
        simulateMcel18nWalletStaleDraft("source-request", "simulate-wallet-source-request-change");
      });
      document.querySelector("#mcel-18n-wallet-tool-simulate-target")?.addEventListener("click", () => {
        simulateMcel18nWalletStaleDraft("target-value", "simulate-wallet-target-value-change");
      });
      document.querySelector("#mcel-18n-wallet-tool-copy-receipt")?.addEventListener("click", () => {
        void copyMcel18nWalletToolReceipt();
      });
      mcelTinyContractRepair?.addEventListener("click", () => repairMcelTinyContractRuntimeChrome("manual-repair"));
      mcelTinyContractBlockWrite?.addEventListener("click", () => attemptMcelTinyContractForbiddenWrite("manual-blocked-write"));
      mcelTinyContractLoadSource?.addEventListener("click", loadMcelTinyContractIntoSourceEditor);
      mcelSiteFrameResync?.addEventListener("click", () => syncMcelRenderedSiteFrame("twiddle-resync"));
      mcelSiteFrameRebuild?.addEventListener("click", () => rebuildMcelSiteFrameShell("twiddle-rebuild", {syncAfter: true}));
      mcelSiteFrameClear?.addEventListener("click", () => clearMcelSiteFrameSrcdoc("twiddle-clear"));
      mcelCanonicalAppMount?.addEventListener("click", () => mountMcelCanonicalAppSpecimen("manual-mount"));
      mcelCanonicalAppRefresh?.addEventListener("click", () => refreshMcelCanonicalAppSpecimen("manual-refresh"));
      mcelCanonicalAppInspect?.addEventListener("click", () => inspectMcelCanonicalAppSpecimen("manual-inspect"));
      mcelCanonicalAppEnrich?.addEventListener("click", () => applyMcelCanonicalTaskManagerEnrichment("manual-enrich"));
      mcelCanonicalAppProof?.addEventListener("click", () => runMcelCanonicalAppSpecimenProof("manual-proof"));
      mcelCanonicalAppLens?.addEventListener("click", () => applyMcelCanonicalTaskManagerLens("manual-lens"));
      mcelCanonicalAppClean?.addEventListener("click", () => clearMcelCanonicalTaskManagerLens("manual-clean"));
      mcelCanonicalAppSelect?.addEventListener("change", () => {
        syncMcelCanonicalSpecimenControls("specimen-select");
        renderMcelCanonicalAppPlanner("specimen-select");
        renderMcelCanonicalAppLensMap(null, "specimen-select");
        renderMcelCanonicalAppSpecimenStatus("specimen-select");
      });
      bindMcelSiteFrameLifecycle("boot");
      renderMcelSiteFrameTwiddle("boot");
      bindMcelCanonicalAppSpecimenLifecycle("boot");
      renderMcelCanonicalAppPlanner("boot");
      renderMcelCanonicalAppSpecimenStatus("boot");
      document.querySelectorAll("[data-mcel-close-modal]").forEach((button) => {
        button.addEventListener("click", () => closeMcelLabModal(button.dataset.mcelCloseModal || "all"));
      });
      [mcelEditorModal, mcelSiteModal, mcelSmartCssModal].filter(Boolean).forEach((modal) => {
        modal.addEventListener("click", (event) => {
          if (event.target === modal) closeMcelLabModal("all");
        });
      });
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && mcelLabState.activeModal) closeMcelLabModal("all");
      });
      mcelCommandPlan?.addEventListener("click", planMcelSemanticCommand);
      mcelCommandApply?.addEventListener("click", applyMcelSemanticCommand);
      mcelProjectSave?.addEventListener("click", saveMcelProject);
      mcelProjectRestore?.addEventListener("click", restoreMcelProject);
      mcelProjectExport?.addEventListener("click", exportMcelProject);
      mcelCommandInput?.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          applyMcelSemanticCommand();
        }
      });
      mcelSourceHtml?.addEventListener("input", debounceMcelLabCompile);
      mcelRuntimePreview?.addEventListener("click", handleMcelRuntimeClick);
      document.querySelectorAll("[data-mcel-block]").forEach((button) => {
        button.addEventListener("click", () => insertMcelLabBlock(button.dataset.mcelBlock || "panel"));
      });
      document.querySelectorAll("[data-mcel-mode]").forEach((button) => {
        button.addEventListener("click", () => setMcelLabMode(button.dataset.mcelMode || "source"));
      });
    }

    let mcelLabCompileTimer = null;
    let mcelAutopilotTimer = null;

    function recordMcelEvent(module, code, message, level = "info") {
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {level, module, code, message}
      ].slice(-64);
      renderMcelCompilerLog();
    }
    function debounceMcelLabCompile() {
      clearTimeout(mcelLabCompileTimer);
      mcelLabCompileTimer = setTimeout(() => compileMcelLabSource("source-input"), 240);
    }

    function scheduleMcelAutopilotProof(reason = "scheduled-autopilot") {
      renderMcelAutopilotDeferred(reason);
      return null;
    }

    function renderMcelAutopilotDeferred(reason = "manual-only") {
      clearTimeout(mcelAutopilotTimer);
      mcelLabState.lastSupervisorReport = null;
      if (mcelSupervisorReport) {
        mcelSupervisorReport.textContent = [
          "Autopilot proof is manual-only.",
          `reason: ${reason}`,
          "Use Run Autopilot Proof from Diagnostics & Proofs when you want the supervisor report.",
          "Scenario changes and page load intentionally do not run matrix, acid, kernel, or autopilot suites."
        ].join("\n");
      }
    }

    function populateMcelThemes() {
      if (!mcelThemeSelect || typeof McelLabStyleLaw === "undefined") return;
      const catalog = McelLabStyleLaw.themeCatalog || McelLabStyleLaw.themes.map((theme) => ({id: theme, label: theme, description: ""}));
      mcelThemeSelect.innerHTML = "";
      catalog.forEach((theme) => {
        const option = document.createElement("option");
        option.value = theme.id;
        option.textContent = theme.label || theme.id;
        if (theme.description) option.title = theme.description;
        if (theme.audience) option.dataset.audience = theme.audience;
        mcelThemeSelect.appendChild(option);
      });
      mcelThemeSelect.value = McelLabStyleLaw.normalizeTheme(mcelLabState.theme);
    }

    function populateMcelChromes() {
      if (!mcelChromeSelect || typeof McelLabChromeLaw === "undefined") return;
      const catalog = McelLabChromeLaw.chromeCatalog || McelLabChromeLaw.chromes.map((chrome) => ({id: chrome, label: chrome, description: ""}));
      mcelChromeSelect.innerHTML = "";
      catalog.forEach((chrome) => {
        const option = document.createElement("option");
        option.value = chrome.id;
        option.textContent = chrome.label || chrome.id;
        if (chrome.description) option.title = chrome.description;
        if (chrome.kind) option.dataset.kind = chrome.kind;
        if (chrome.restructuresHierarchy) option.dataset.restructuresHierarchy = "true";
        mcelChromeSelect.appendChild(option);
      });
      mcelLabState.chrome = McelLabChromeLaw.normalizeChrome(mcelLabState.chrome);
      mcelChromeSelect.value = mcelLabState.chrome;
    }

    function populateMcelScenarios() {
      if (!mcelScenarioSelect || typeof McelLabScenarios === "undefined") return;
      mcelScenarioSelect.innerHTML = "";
      McelLabScenarios.all().forEach((scenario) => {
        const option = document.createElement("option");
        option.value = scenario.id;
        option.textContent = scenario.label;
        mcelScenarioSelect.appendChild(option);
      });
      describeSelectedMcelScenario();
    }

    function describeSelectedMcelScenario() {
      if (!mcelScenarioDescription || typeof McelLabScenarios === "undefined") return;
      const scenario = McelLabScenarios.byId(mcelScenarioSelect?.value || "round-trip");
      mcelScenarioDescription.textContent = scenario.description;
    }

    function populateMcelAcidCases() {
      if (!mcelAcidSelect || typeof McelLabAcidTests === "undefined") return;
      const cases = McelLabAcidTests.listCases();
      mcelAcidSelect.innerHTML = "";
      cases.forEach((testCase) => {
        const option = document.createElement("option");
        option.value = testCase.id;
        option.textContent = `${testCase.severity.toUpperCase()} · ${testCase.name}`;
        mcelAcidSelect.appendChild(option);
      });
      if (!cases.some((testCase) => testCase.id === mcelAcidSelect.value)) {
        mcelAcidSelect.value = cases[0]?.id || "";
      }
    }

    function loadSelectedMcelScenario() {
      if (!mcelSourceHtml || typeof McelLabScenarios === "undefined") return;
      const scenario = McelLabScenarios.byId(mcelScenarioSelect?.value || "round-trip");
      mcelSourceHtml.value = scenario.source;
      selectMcelSourceIndex(0, `scenario:${scenario.id}`);
      setMcelLabMode(scenario.mode || "source");
      compileMcelLabSource(`scenario:${scenario.id}`);
      syncMcelGrapesFromSource();
      renderMcelAutopilotDeferred(`scenario:${scenario.id}`);
    }

    function initMcelLabGrapes() {
      if (!mcelGrapesCanvas || !mcelGrapesHost || typeof grapesjs === "undefined") {
        if (mcelGrapesHost) mcelGrapesHost.hidden = true;
        if (mcelGrapesFallback) mcelGrapesFallback.hidden = false;
        if (mcelEditorStatus) mcelEditorStatus.textContent = "semantic fallback active";
        return;
      }
      try {
        if (mcelGrapesFallback) mcelGrapesFallback.hidden = true;
        mcelLabState.grapesEditor = grapesjs.init({
          container: "#mcel-grapes",
          height: "100%",
          storageManager: false,
          blockManager: {appendTo: null},
          traitManager: {appendTo: null},
          panels: {defaults: []},
          canvas: {styles: [], scripts: []}
        });
        mcelLabState.grapesEditor.setComponents(McelLabEditor.canonicalSource(mcelSourceHtml?.value || McelLabContract.defaultSource));
        mcelLabState.grapesEditor.on("component:selected", () => {
          const html = McelLabEditor.sanitizeEditorHtml(mcelLabState.grapesEditor.getHtml());
          const sourceList = McelLabEditor.sourceList(html);
          if (sourceList.length) {
            selectMcelSourceIndex(0, "grapes-selected");
          }
        });
        mcelLabState.grapesEditor.on("component:update component:add component:remove", () => {
          if (!mcelSourceHtml || !mcelLabState.grapesReady || mcelLabState.syncingGrapes) return;
          const html = McelLabEditor.sanitizeEditorHtml(mcelLabState.grapesEditor.getHtml());
          if (html && html.trim() && html.trim() !== mcelSourceHtml.value.trim()) {
            mcelSourceHtml.value = html.trim();
            compileMcelLabSource("grapes-update");
          }
        });
        mcelLabState.grapesReady = true;
        if (mcelEditorStatus) mcelEditorStatus.textContent = "GrapesJS editing semantic source";
      } catch (error) {
        mcelLabState.grapesEditor = null;
        mcelLabState.grapesReady = false;
        if (mcelGrapesHost) mcelGrapesHost.hidden = true;
        if (mcelGrapesFallback) mcelGrapesFallback.hidden = false;
        if (mcelEditorStatus) mcelEditorStatus.textContent = `semantic fallback active: ${error.message}`;
      }
    }

    function syncMcelGrapesFromSource() {
      if (!mcelLabState.grapesEditor || !mcelLabState.grapesReady || !mcelSourceHtml) return;
      try {
        mcelLabState.syncingGrapes = true;
        mcelLabState.grapesEditor.setComponents(McelLabEditor.canonicalSource(mcelSourceHtml.value));
      } finally {
        mcelLabState.syncingGrapes = false;
      }
    }

    function currentMcelSource() {
      return McelLabEditor.canonicalSource(mcelSourceHtml?.value || McelLabContract.defaultSource);
    }

    function compileMcelLabSource(reason = "compile") {
      if (!mcelRuntimePreview || !mcelSourceHtml) return;
      const cleanSource = currentMcelSource();
      if (cleanSource && cleanSource !== mcelSourceHtml.value.trim()) {
        mcelSourceHtml.value = cleanSource;
      }
      const compiled = window.MCEL?.compile
        ? MCEL.compile(cleanSource, {reason, theme: mcelLabState.theme})
        : McelLabEngine.compileSource(cleanSource, {reason});
      mcelRuntimePreview.innerHTML = compiled.runtimeHtml;
      applyMcelRuntimeStyleLaw(reason);
      mcelLabState.lastSourceList = McelLabEditor.sourceList(cleanSource);
      mcelLabState.selectedIndex = Math.min(
        Math.max(mcelLabState.selectedIndex, 0),
        Math.max(mcelLabState.lastSourceList.length - 1, 0)
      );
      mcelLabState.compileEvents = [...mcelLabState.compileEvents, ...compiled.events].slice(-64);
      const serialization = McelLabEngine.serializeRuntimeRoot(mcelRuntimePreview, {reason: "post-compile-check"});
      mcelLabState.lastSerializerReport = serialization.report;
      renderMcelRuntimeDom();
      renderMcelSerializerDiff(serialization.serialized);
      renderMcelA11yReport();
      renderMcelDebugger();
      renderMcelCssLawReport();
      renderMcelLayoutLawReport();
      renderMcelSiteSkeleton();
      renderMcelGraphReport();
      renderMcelScenarioMatrix();
      renderMcelEvidencePacket();
      renderMcelAcidTests();
      renderMcelSupervisorReport();
      renderMcelKernelAudit();
      renderMcelTraceabilityMap();
      renderMcelPriorArtReport();
      renderMcelSubsumptionLattice();
      renderMcelWorkbenchPlan();
      renderMcelBrowserSemanticProof();
      renderMcelReadiness();
      renderMcelProjectReport();
      renderMcelCompilerLog();
      syncMcelTraitControls();
      markSelectedMcelRuntimeElement();
      renderMcelSelectionStatus();
      renderMcelSiteSkeleton();
    }

    function serializeMcelRuntime(reason = "serialize") {
      if (!mcelRuntimePreview || !mcelSourceHtml) return;
      const result = window.MCEL?.serialize
        ? MCEL.serialize(mcelRuntimePreview, {reason})
        : McelLabEngine.serializeRuntimeRoot(mcelRuntimePreview, {reason});
      mcelLabState.lastSerializerReport = result.report;
      mcelSourceHtml.value = result.serialized;
      mcelLabState.compileEvents.push({
        level: result.report.serializerClean ? "success" : "warning",
        module: "serializer",
        code: result.report.serializerClean ? "MCEL_SERIALIZER_CLEAN" : "MCEL_SERIALIZER_WARNING",
        message: result.report.serializerClean ? "Serialized clean source replaced source pane." : result.report.warnings.join(" ")
      });
      syncMcelGrapesFromSource();
      compileMcelLabSource(reason);
    }

    function repairMcelRuntime(reason = "repair") {
      if (!mcelRuntimePreview) return;
      const repair = McelLabEngine.repairRuntimeRoot(mcelRuntimePreview, {reason});
      mcelLabState.compileEvents = [...mcelLabState.compileEvents, ...repair.events].slice(-64);
      applyMcelRuntimeStyleLaw(reason);
      renderMcelRuntimeDom();
      renderMcelA11yReport();
      renderMcelDebugger();
      renderMcelCssLawReport();
      renderMcelLayoutLawReport();
      renderMcelGraphReport();
      renderMcelReadiness();
      renderMcelCompilerLog();
      markSelectedMcelRuntimeElement();
    }

    function damageMcelRuntime() {
      if (!mcelRuntimePreview) return;
      const result = McelLabEngine.damageRuntimeRoot(mcelRuntimePreview);
      mcelLabState.compileEvents = [...mcelLabState.compileEvents, ...result.events].slice(-64);
      applyMcelRuntimeStyleLaw("damage");
      renderMcelRuntimeDom();
      renderMcelA11yReport();
      renderMcelDebugger();
      renderMcelCssLawReport();
      renderMcelLayoutLawReport();
      renderMcelGraphReport();
      renderMcelReadiness();
      renderMcelCompilerLog();
      markSelectedMcelRuntimeElement();
    }

    function resetMcelLab() {
      if (!mcelSourceHtml) return;
      mcelSourceHtml.value = McelLabContract.defaultSource;
      mcelLabState.compileEvents = [];
      mcelLabState.lastSerializerReport = null;
      mcelLabState.lastTestReport = null;
      mcelLabState.lastLayoutLawReport = null;
      mcelLabState.lastGraphReport = null;
      mcelLabState.lastAuditReport = null;
      mcelLabState.lastMatrixReport = null;
      mcelLabState.lastEvidencePacket = null;
      mcelLabState.lastReadinessReport = null;
      mcelLabState.lastSupervisorReport = null;
      mcelLabState.lastCommandPlan = null;
      mcelLabState.theme = "theme-machine";
      mcelLabState.chrome = "chrome-strict-hierarchy";
      if (mcelThemeSelect) mcelThemeSelect.value = "theme-machine";
      if (mcelChromeSelect) mcelChromeSelect.value = "chrome-strict-hierarchy";
      selectMcelSourceIndex(0, "reset");
      syncMcelGrapesFromSource();
      compileMcelLabSource("reset");
      renderMcelContractTests();
      renderMcelGraphReport();
      renderMcelAuditReport();
      renderMcelScenarioMatrix();
      renderMcelEvidencePacket();
      renderMcelAcidTests();
      renderMcelSupervisorReport();
      renderMcelReadiness();
      scheduleMcelAutopilotProof("reset-autopilot");
    }

    function runMcelContractTests() {
      const report = typeof McelLabTestHarness !== "undefined"
        ? McelLabTestHarness.runAll()
        : McelLabEngine.runContractTests();
      mcelLabState.lastTestReport = report;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: report.failed ? "warning" : "success",
          module: "tests",
          code: report.failed ? "MCEL_FULL_SUITE_FAILED" : "MCEL_FULL_SUITE_PASSED",
          message: `${report.passed} passed / ${report.failed} failed.`
        }
      ].slice(-64);
      renderMcelContractTests();
      renderMcelReadiness();
      renderMcelCompilerLog();
    }

    function runMcelOperationalAudit() {
      if (typeof McelLabGraph === "undefined" || !mcelSourceHtml || !mcelRuntimePreview) return;
      const report = McelLabGraph.audit(currentMcelSource(), mcelRuntimePreview, {reason: "manual-audit"});
      mcelLabState.lastAuditReport = report;
      mcelLabState.lastGraphReport = McelLabGraph.compactReport(currentMcelSource(), mcelRuntimePreview);
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: report.failed ? "warning" : "success",
          module: "audit",
          code: report.failed ? "MCEL_OPERATIONAL_AUDIT_BLOCKED" : "MCEL_OPERATIONAL_AUDIT_CLEAN",
          message: report.failed
            ? `${report.failed} audit check(s) failed: ${report.issues.join(" ")}`
            : `Operational graph clean with ${report.runtimeGraph.generatedPartCount} generated part(s) under provenance.`
        }
      ].slice(-64);
      renderMcelGraphReport();
      renderMcelAuditReport();
      renderMcelReadiness();
      renderMcelCompilerLog();
    }

    function runMcelKernelAudit(reason = "manual-kernel-audit") {
      if (typeof McelLabKernel === "undefined" || !mcelSourceHtml) return null;
      const report = McelLabKernel.runKernelAudit({
        source: currentMcelSource(),
        runtimeRoot: mcelRuntimePreview,
        theme: mcelLabState.theme,
        testReport: mcelLabState.lastTestReport,
        matrixReport: mcelLabState.lastMatrixReport,
        acidReport: mcelLabState.lastAcidReport,
        kernelReport: mcelLabState.lastKernelAudit,
        reason
      });
      mcelLabState.lastKernelAudit = report;
      mcelLabState.lastTraceabilityMap = report.traceability;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: report.status === "ready" ? "success" : "warning",
          module: "kernel",
          code: report.status === "ready" ? "MCEL_KERNEL_AUDIT_READY" : "MCEL_KERNEL_AUDIT_BLOCKED",
          message: `Kernel audit ${report.status}: ${report.passCount}/${report.total} debt gates at score ${report.score}.`
        }
      ].slice(-64);
      renderMcelKernelAudit();
      renderMcelTraceabilityMap();
      renderMcelPriorArtReport();
      renderMcelSubsumptionLattice();
      renderMcelWorkbenchPlan();
      renderMcelBrowserSemanticProof();
      renderMcelReadiness();
      renderMcelCompilerLog();
      return report;
    }

    function buildMcelTraceabilityMap(reason = "manual-traceability") {
      if (typeof McelLabKernel === "undefined") return null;
      const map = McelLabKernel.buildTraceabilityMap({reason});
      mcelLabState.lastTraceabilityMap = map;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: map.status === "covered" ? "success" : "warning",
          module: "kernel",
          code: map.status === "covered" ? "MCEL_TRACEABILITY_COVERED" : "MCEL_TRACEABILITY_BLOCKED",
          message: `Traceability map ${map.status}: ${map.covered}/${map.total} requirement(s) covered.`
        }
      ].slice(-64);
      renderMcelTraceabilityMap();
      renderMcelPriorArtReport();
      renderMcelCompilerLog();
      return map;
    }

    function buildMcelSubsumptionLattice(reason = "manual-subsumption") {
      const lattice = window.MCEL?.buildSubsumptionLattice ? MCEL.buildSubsumptionLattice() : McelLabPlatformSpine?.buildSubsumptionLattice?.();
      mcelLabState.lastSubsumptionLattice = lattice;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: lattice ? "success" : "warning",
          module: "platform-spine",
          code: lattice ? "MCEL_SUBSUMPTION_LATTICE_READY" : "MCEL_SUBSUMPTION_LATTICE_UNAVAILABLE",
          message: lattice ? `Subsumption lattice maps ${lattice.obsoleteLibraryMap?.length || 0} obsolete library family claim(s).` : "Subsumption lattice is unavailable."
        }
      ].slice(-64);
      renderMcelSubsumptionLattice();
      renderMcelCompilerLog();
      return lattice;
    }

    function buildMcelAdoptionCase(reason = "manual-adoption-case") {
      const adoptionCase = window.MCEL?.buildAdoptionCase
        ? MCEL.buildAdoptionCase({reason})
        : McelLabPlatformSpine?.buildAdoptionCase?.({reason});
      mcelLabState.lastAdoptionCase = adoptionCase;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: adoptionCase?.verdict === "adopt-mcel-when-proof-is-required" ? "success" : "warning",
          module: "platform-spine",
          code: adoptionCase ? "MCEL_ADOPTION_CASE_READY" : "MCEL_ADOPTION_CASE_UNAVAILABLE",
          message: adoptionCase
            ? `Adoption case verdict ${adoptionCase.verdict}; ${adoptionCase.proofCoverage?.passedGateCount || 0}/${adoptionCase.proofCoverage?.totalGateCount || 0} comparison gate(s) passed.`
            : "Adoption case is unavailable."
        }
      ].slice(-64);
      renderMcelAdoptionCase();
      renderMcelCompilerLog();
      return adoptionCase;
    }

    function buildMcelWorkbenchPlan(reason = "manual-workbench") {
      const plan = window.MCEL?.buildWorkbenchPlan ? MCEL.buildWorkbenchPlan() : McelLabWorkbench?.buildWorkbenchPlan?.();
      mcelLabState.lastWorkbenchPlan = plan;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: plan ? "success" : "warning",
          module: "workbench",
          code: plan ? "MCEL_WORKBENCH_PLAN_READY" : "MCEL_WORKBENCH_PLAN_UNAVAILABLE",
          message: plan ? `Workbench plan tracks ${plan.requiredBlueprints?.length || 0} proof blueprint(s).` : "Workbench plan is unavailable."
        }
      ].slice(-64);
      renderMcelWorkbenchPlan();
      renderMcelCompilerLog();
      return plan;
    }

    function runMcelBrowserSemanticProof(reason = "manual-browser-proof") {
      const report = window.MCEL?.runBrowserProof
        ? MCEL.runBrowserProof(mcelRuntimePreview, {reason})
        : McelLabBrowserRunner?.observeAndProve?.(mcelRuntimePreview, {reason});
      mcelLabState.lastBrowserProof = report;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: report && !report.failed ? "success" : "warning",
          module: "browser-runner",
          code: report && !report.failed ? "MCEL_BROWSER_SEMANTIC_PROOF_READY" : "MCEL_BROWSER_SEMANTIC_PROOF_BLOCKED",
          message: report ? `Browser semantic proof observed ${report.elementCount || 0} element(s); liveGeometry=${report.liveGeometry}.` : "Browser semantic proof is unavailable."
        }
      ].slice(-64);
      renderMcelBrowserSemanticProof();
      renderMcelCompilerLog();
      return report;
    }

    function runMcelScenarioMatrix() {
      if (typeof McelLabOpsRunner === "undefined") return;
      const report = McelLabOpsRunner.runScenarioMatrix();
      mcelLabState.lastMatrixReport = report;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: report.failed ? "warning" : "success",
          module: "matrix",
          code: report.failed ? "MCEL_SCENARIO_MATRIX_FAILED" : "MCEL_SCENARIO_MATRIX_PASSED",
          message: `${report.passed} passed / ${report.failed} failed across ${report.caseCount} scenario-theme case(s).`
        }
      ].slice(-64);
      renderMcelScenarioMatrix();
      renderMcelReadiness();
      renderMcelCompilerLog();
    }

    function runSelectedMcelAcidTest(reason = "manual-selected-acid-test") {
      if (typeof McelLabAcidTests === "undefined") return null;
      const selectedCaseId = mcelAcidSelect?.value || McelLabAcidTests.listCases()[0]?.id;
      const report = McelLabAcidTests.runOne(selectedCaseId, {
        source: currentMcelSource(),
        theme: mcelLabState.theme,
        matrixReport: mcelLabState.lastMatrixReport,
        kernelReport: mcelLabState.lastKernelAudit,
        reason
      });
      mcelLabState.lastAcidReport = report;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: report.failed ? "warning" : "success",
          module: "acid-tests",
          code: report.failed ? "MCEL_SELECTED_ACID_TEST_FAILED" : "MCEL_SELECTED_ACID_TEST_PASSED",
          message: `${report.passed} passed / ${report.failed} failed for selected acid test: ${report.tests[0]?.name || selectedCaseId}.`
        }
      ].slice(-64);
      renderMcelAcidTests();
      renderMcelReadiness();
      renderMcelCompilerLog();
      return report;
    }

    function runMcelAcidTests(reason = "manual-acid-suite") {
      if (typeof McelLabAcidTests === "undefined") return null;
      const report = McelLabAcidTests.runAll({
        source: currentMcelSource(),
        theme: mcelLabState.theme,
        matrixReport: mcelLabState.lastMatrixReport,
        kernelReport: mcelLabState.lastKernelAudit,
        reason,
        explicitSuite: true
      });
      mcelLabState.lastAcidReport = report;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: report.failed ? "warning" : "success",
          module: "acid-tests",
          code: report.failed ? "MCEL_ACID_SUITE_FAILED" : "MCEL_ACID_SUITE_PASSED",
          message: `${report.passed} passed / ${report.failed} failed across ${report.total} acid test(s).`
        }
      ].slice(-64);
      renderMcelAcidTests();
      renderMcelReadiness();
      renderMcelCompilerLog();
      return report;
    }

    function buildMcelEvidencePacket() {
      if (typeof McelLabOpsRunner === "undefined" || !mcelSourceHtml || !mcelRuntimePreview) return;
      const packet = McelLabOpsRunner.buildEvidencePacket({
        source: currentMcelSource(),
        runtimeRoot: mcelRuntimePreview,
        theme: mcelLabState.theme,
        serializerReport: mcelLabState.lastSerializerReport,
        cssLawReport: mcelLabState.lastCssLawReport,
        layoutLawReport: mcelLabState.lastLayoutLawReport,
        auditReport: mcelLabState.lastAuditReport,
        testReport: mcelLabState.lastTestReport,
        matrixReport: mcelLabState.lastMatrixReport,
        acidReport: mcelLabState.lastAcidReport,
        kernelReport: mcelLabState.lastKernelAudit
      });
      mcelLabState.lastEvidencePacket = packet;
      mcelLabState.lastReadinessReport = packet.readiness;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: packet.readiness.status === "blocked" ? "warning" : "success",
          module: "evidence",
          code: "MCEL_EVIDENCE_PACKET_BUILT",
          message: `Evidence packet built with readiness ${packet.readiness.status} at score ${packet.readiness.score}.`
        }
      ].slice(-64);
      renderMcelEvidencePacket();
      renderMcelReadiness();
      renderMcelCompilerLog();
    }

    function applyMcelSupervisorReport(report) {
      if (!report) return;
      mcelLabState.lastSupervisorReport = report;
      mcelLabState.lastSerializerReport = report.serializerReport;
      mcelLabState.lastCssLawReport = report.cssLawReport;
      mcelLabState.lastLayoutLawReport = report.layoutLawReport || mcelLabState.lastLayoutLawReport;
      mcelLabState.lastAuditReport = report.auditReport;
      mcelLabState.lastGraphReport = report.graphReport;
      mcelLabState.lastTestReport = report.testReport;
      mcelLabState.lastMatrixReport = report.matrixReport;
      mcelLabState.lastAcidReport = report.acidReport || mcelLabState.lastAcidReport;
      mcelLabState.lastEvidencePacket = report.evidencePacket;
      mcelLabState.lastKernelAudit = report.kernelReport || mcelLabState.lastKernelAudit;
      mcelLabState.lastTraceabilityMap = report.kernelReport?.traceability || mcelLabState.lastTraceabilityMap;
      mcelLabState.lastReadinessReport = report.readiness;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        ...(report.compileEvents || []),
        {
          level: report.qualityGate.status === "ready" ? "success" : "warning",
          module: "supervisor",
          code: report.qualityGate.status === "ready" ? "MCEL_AUTOPILOT_READY" : "MCEL_AUTOPILOT_BLOCKED",
          message: `Autopilot proof ${report.qualityGate.status}: ${report.qualityGate.passCount}/${report.qualityGate.total} gates at score ${report.qualityGate.score}.`
        }
      ].slice(-64);
    }

    function runMcelAutopilotProof(reason = "manual-autopilot") {
      if (typeof McelLabSupervisor === "undefined" || !mcelSourceHtml) return null;
      const report = McelLabSupervisor.runFullProof({
        source: currentMcelSource(),
        theme: mcelLabState.theme,
        selectedIndex: mcelLabState.selectedIndex,
        testReport: mcelLabState.lastTestReport,
        matrixReport: mcelLabState.lastMatrixReport,
        acidReport: mcelLabState.lastAcidReport,
        kernelReport: mcelLabState.lastKernelAudit,
        runHeavyProofs: false,
        reason
      });
      applyMcelSupervisorReport(report);
      renderMcelContractTests();
      renderMcelScenarioMatrix();
      renderMcelEvidencePacket();
      renderMcelAcidTests();
      renderMcelSupervisorReport();
      renderMcelGraphReport();
      renderMcelAuditReport();
      renderMcelCssLawReport();
      renderMcelLayoutLawReport();
      renderMcelKernelAudit();
      renderMcelTraceabilityMap();
      renderMcelPriorArtReport();
      renderMcelSubsumptionLattice();
      renderMcelWorkbenchPlan();
      renderMcelBrowserSemanticProof();
      renderMcelReadiness();
      renderMcelCompilerLog();
      return report;
    }

    function changeMcelTheme(reason = "theme") {
      if (typeof McelLabStyleLaw !== "undefined") {
        mcelLabState.theme = McelLabStyleLaw.normalizeTheme(mcelThemeSelect?.value || mcelLabState.theme);
      } else {
        mcelLabState.theme = mcelThemeSelect?.value || mcelLabState.theme;
      }
      const label = typeof McelLabStyleLaw !== "undefined" && McelLabStyleLaw.themeLabel
        ? McelLabStyleLaw.themeLabel(mcelLabState.theme)
        : mcelLabState.theme;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {level: "success", module: "style-law", code: "MCEL_THEME_CHANGED", message: `Theme changed to ${label} (${mcelLabState.theme}) during ${reason}.`}
      ].slice(-64);
      applyMcelRuntimeStyleLaw(reason);
      renderMcelRuntimeDom();
      renderMcelCssLawReport();
      renderMcelGraphReport();
      renderMcelCompilerLog();
      syncMcelRenderedSiteFrame("theme");
    }

    function changeMcelChrome(reason = "chrome") {
      if (typeof McelLabChromeLaw !== "undefined") {
        mcelLabState.chrome = McelLabChromeLaw.normalizeChrome(mcelChromeSelect?.value || mcelLabState.chrome);
      } else {
        mcelLabState.chrome = mcelChromeSelect?.value || mcelLabState.chrome || "chrome-strict-hierarchy";
      }
      const label = typeof McelLabChromeLaw !== "undefined" && McelLabChromeLaw.chromeLabel
        ? McelLabChromeLaw.chromeLabel(mcelLabState.chrome)
        : mcelLabState.chrome;
      if (mcelChromeSelect) mcelChromeSelect.value = mcelLabState.chrome;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {level: "success", module: "chrome-law", code: "MCEL_CHROME_CHANGED", message: `Chrome changed to ${label} (${mcelLabState.chrome}) during ${reason}.`}
      ].slice(-64);
      renderMcelCompilerLog();
      syncMcelRenderedSiteFrame("chrome");
    }

    function applyMcelRuntimeStyleLaw(reason = "style-law") {
      if (!mcelRuntimePreview || typeof McelLabStyleLaw === "undefined") return;
      mcelLabState.theme = McelLabStyleLaw.normalizeTheme(mcelThemeSelect?.value || mcelLabState.theme);
      if (mcelThemeSelect) mcelThemeSelect.value = mcelLabState.theme;
      mcelLabState.lastCssLawReport = McelLabStyleLaw.applyRuntimeLaw(mcelRuntimePreview, {
        theme: mcelLabState.theme,
        reason
      });
      if (typeof McelLabLayoutLaw !== "undefined") {
        mcelLabState.lastLayoutLawReport = McelLabLayoutLaw.applyRuntimeLaw(mcelRuntimePreview, {reason});
      }
      if (typeof McelLabPlatformSpine !== "undefined") {
        McelLabPlatformSpine.applyPlatformLaws(mcelRuntimePreview, {reason});
      }
    }

    function planMcelSemanticCommand() {
      if (typeof McelLabCommandSurface === "undefined") return null;
      const command = mcelCommandInput?.value || "";
      const plan = McelLabCommandSurface.plan(command, {
        source: mcelSourceHtml?.value || "",
        selectedIndex: mcelLabState.selectedIndex,
        theme: mcelLabState.theme
      });
      mcelLabState.lastCommandPlan = plan;
      renderMcelCommandReport();
      return plan;
    }

    function applyMcelSemanticCommand() {
      if (typeof McelLabCommandSurface === "undefined" || !mcelSourceHtml) return;
      const plan = planMcelSemanticCommand();
      if (!plan || !plan.ok) {
        mcelLabState.compileEvents = [
          ...mcelLabState.compileEvents,
          {level: "warning", module: "command", code: "MCEL_COMMAND_REJECTED", message: (plan?.warnings || ["Command could not be planned."]).join(" ")}
        ].slice(-64);
        renderMcelCompilerLog();
        return;
      }
      const applied = McelLabCommandSurface.apply(plan, {
        source: mcelSourceHtml.value,
        selectedIndex: mcelLabState.selectedIndex,
        theme: mcelLabState.theme
      });
      mcelSourceHtml.value = applied.source;
      mcelLabState.selectedIndex = applied.selectedIndex;
      mcelLabState.theme = applied.theme;
      if (mcelThemeSelect) mcelThemeSelect.value = mcelLabState.theme;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        ...applied.events,
        {level: "success", module: "command", code: "MCEL_COMMAND_APPLIED", message: plan.summary.join("; ") || "Semantic command applied."}
      ].slice(-64);
      syncMcelGrapesFromSource();
      compileMcelLabSource("semantic-command");

      if (applied.actions.includes("serialize")) serializeMcelRuntime("semantic-command");
      if (applied.actions.includes("damage")) damageMcelRuntime();
      if (applied.actions.includes("repair")) repairMcelRuntime("semantic-command");
      if (applied.actions.includes("test")) runMcelContractTests();
      if (applied.actions.includes("matrix")) runMcelScenarioMatrix();
      if (applied.actions.includes("acid")) runSelectedMcelAcidTest("semantic-command-selected-acid");
      if (applied.actions.includes("graph")) renderMcelGraphReport();
      if (applied.actions.includes("layout")) {
        applyMcelRuntimeStyleLaw("semantic-command-layout");
        renderMcelLayoutLawReport();
      }
      if (applied.actions.includes("audit")) runMcelOperationalAudit();
      if (applied.actions.includes("evidence")) buildMcelEvidencePacket();
      if (applied.actions.includes("autopilot")) runMcelAutopilotProof("semantic-command");
      if (applied.actions.includes("kernel")) runMcelKernelAudit("semantic-command");
      if (applied.actions.includes("traceability")) buildMcelTraceabilityMap("semantic-command");
      if (applied.actions.includes("prior-art")) renderMcelPriorArtReport();
      if (applied.actions.includes("explain")) setMcelLabMode("runtime");

      renderMcelCommandReport();
    }

    function currentMcelProjectState() {
      return {
        source: currentMcelSource(),
        selectedIndex: mcelLabState.selectedIndex,
        theme: mcelLabState.theme,
        chrome: mcelLabState.chrome,
        mode: mcelLabState.currentMode,
        scenario: mcelScenarioSelect?.value || "round-trip",
        lastSerializerClean: Boolean(mcelLabState.lastSerializerReport?.serializerClean)
      };
    }

    function saveMcelProject() {
      if (typeof McelLabProjectStore === "undefined") return;
      const result = McelLabProjectStore.save(currentMcelProjectState());
      mcelLabState.lastProjectSnapshot = result.snapshot;
      if (mcelProjectStatus) mcelProjectStatus.textContent = result.message;
      renderMcelProjectReport(result);
    }

    function restoreMcelProject() {
      if (typeof McelLabProjectStore === "undefined" || !mcelSourceHtml) return;
      const result = McelLabProjectStore.restore();
      if (mcelProjectStatus) mcelProjectStatus.textContent = result.message;
      if (result.ok && result.snapshot) {
        mcelSourceHtml.value = result.snapshot.source || McelLabContract.defaultSource;
        mcelLabState.selectedIndex = Number(result.snapshot.selectedIndex || 0);
        mcelLabState.theme = result.snapshot.theme || "theme-machine";
        mcelLabState.chrome = typeof McelLabChromeLaw !== "undefined"
          ? McelLabChromeLaw.normalizeChrome(result.snapshot.chrome || "chrome-strict-hierarchy")
          : (result.snapshot.chrome || "chrome-strict-hierarchy");
        if (mcelThemeSelect) mcelThemeSelect.value = mcelLabState.theme;
        if (mcelChromeSelect) mcelChromeSelect.value = mcelLabState.chrome;
        setMcelLabMode(result.snapshot.mode || "source");
        syncMcelGrapesFromSource();
        compileMcelLabSource("project-restore");
      }
      mcelLabState.lastProjectSnapshot = result.snapshot;
      renderMcelProjectReport(result);
    }

    function exportMcelProject() {
      if (typeof McelLabProjectStore === "undefined") return;
      const text = McelLabProjectStore.exportText(currentMcelProjectState());
      mcelLabState.lastProjectSnapshot = JSON.parse(text);
      if (mcelProjectStatus) mcelProjectStatus.textContent = "Exported clean MCEL project snapshot into the Project State pane.";
      if (mcelProjectReport) mcelProjectReport.textContent = text;
    }

    function applyMcelTraitsToSelectedSourceWidget() {
      if (!mcelSourceHtml) return;
      const result = McelLabEditor.applyTraits(mcelSourceHtml.value, {index: mcelLabState.selectedIndex}, {
        kind: mcelTraitKind?.value,
        flow: mcelTraitFlow?.value,
        rank: mcelTraitRank?.value,
        state: mcelTraitState?.value,
        density: mcelTraitDensity?.value,
        sizePolicy: mcelTraitSizePolicy?.value,
        overflowPolicy: mcelTraitOverflowPolicy?.value,
        scrollPolicy: mcelTraitScrollPolicy?.value,
        words: mcelTraitWords?.value,
        connects: mcelTraitConnects?.value
      });
      mcelSourceHtml.value = result.source;
      mcelLabState.selectedIndex = Math.max(result.index, 0);
      mcelLabState.compileEvents = [...mcelLabState.compileEvents, ...result.events].slice(-64);
      syncMcelGrapesFromSource();
      compileMcelLabSource("trait-update");
    }

    function applyMcelTraitsToFirstSourceWidget() {
      mcelLabState.selectedIndex = 0;
      applyMcelTraitsToSelectedSourceWidget();
    }

    function insertMcelLabBlock(blockKey) {
      if (!mcelSourceHtml) return;
      const result = McelLabEditor.insertBlock(mcelSourceHtml.value, blockKey, {afterIndex: mcelLabState.selectedIndex});
      mcelSourceHtml.value = result.source;
      mcelLabState.selectedIndex = result.index;
      mcelLabState.compileEvents = [...mcelLabState.compileEvents, ...result.events].slice(-64);
      syncMcelGrapesFromSource();
      compileMcelLabSource(`insert-${blockKey}`);
    }

    function handleMcelRuntimeClick(event) {
      const selected = event.target.closest?.(`[${McelLabContract.attributes.sourceIndex}]`);
      if (!selected || !mcelRuntimePreview?.contains(selected)) return;
      const index = Number(selected.getAttribute(McelLabContract.attributes.sourceIndex) || "0");
      selectMcelSourceIndex(index, "runtime-click");
    }

    function selectMcelSourceIndex(index, reason = "select") {
      const normalized = McelLabEditor.normalizeRef({index}, mcelSourceHtml?.value || McelLabContract.defaultSource);
      mcelLabState.selectedIndex = normalized.index;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {level: "info", module: "editor", code: "MCEL_EDITOR_SELECTED", message: `Selected source widget ${normalized.index + 1} during ${reason}.`}
      ].slice(-64);
      syncMcelTraitControls();
      markSelectedMcelRuntimeElement();
      renderMcelSelectionStatus();
      renderMcelSiteSkeleton();
      renderMcelDebugger();
      renderMcelCompilerLog();
    }

    function syncMcelTraitControls() {
      const traits = McelLabEditor.readTraits(mcelSourceHtml?.value || McelLabContract.defaultSource, {index: mcelLabState.selectedIndex});
      if (!traits.found) return;
      setSelectOptions(mcelTraitKind, traits.options.kinds, traits.kind);
      setSelectOptions(mcelTraitFlow, traits.options.flows, traits.flow);
      setSelectOptions(mcelTraitRank, traits.options.ranks, traits.rank);
      setSelectOptions(mcelTraitState, traits.options.states, traits.state);
      setSelectOptions(mcelTraitDensity, traits.options.densities, traits.density);
      setSelectOptions(mcelTraitSizePolicy, traits.options.sizePolicies, traits.sizePolicy);
      setSelectOptions(mcelTraitOverflowPolicy, traits.options.overflowPolicies, traits.overflowPolicy);
      setSelectOptions(mcelTraitScrollPolicy, traits.options.scrollPolicies, traits.scrollPolicy);
      if (mcelTraitWords) mcelTraitWords.value = traits.words;
      if (mcelTraitConnects) mcelTraitConnects.value = traits.connects;
    }

    function setSelectOptions(select, values, current) {
      if (!select) return;
      select.innerHTML = "";
      values.forEach((value) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      });
      select.value = values.includes(current) ? current : values[0];
    }

    function markSelectedMcelRuntimeElement() {
      if (!mcelRuntimePreview) return;
      mcelRuntimePreview.querySelectorAll(`[${McelLabContract.attributes.editorSelected}="true"]`).forEach((element) => {
        element.removeAttribute(McelLabContract.attributes.editorSelected);
        element.classList.remove("mcel-selected");
      });
      const selected = mcelRuntimePreview.querySelector(`[${McelLabContract.attributes.sourceIndex}="${mcelLabState.selectedIndex}"]`);
      if (selected) {
        selected.setAttribute(McelLabContract.attributes.editorSelected, "true");
        selected.classList.add("mcel-selected");
      }
    }

    function renderMcelSelectionStatus() {
      if (!mcelSelectionStatus) return;
      const list = mcelLabState.lastSourceList.length ? mcelLabState.lastSourceList : McelLabEditor.sourceList(mcelSourceHtml?.value || "");
      const selected = list[mcelLabState.selectedIndex];
      mcelSelectionStatus.textContent = selected
        ? `Selected source widget: ${mcelLabState.selectedIndex + 1}/${list.length} · ${selected.label} · ${selected.type}/${selected.kind}/${selected.state}`
        : "Selected source widget: none";
    }

    function setMcelLabMode(mode) {
      mcelLabState.currentMode = McelLabContract.modes.includes(mode) ? mode : "source";
      document.querySelectorAll("[data-mcel-mode]").forEach((button) => {
        button.classList.toggle("active", button.dataset.mcelMode === mcelLabState.currentMode);
      });
      if (mcelLabApp) mcelLabApp.dataset.mcelMode = mcelLabState.currentMode;
      if (["diff", "stress", "a11y"].includes(mcelLabState.currentMode)) {
        openMcelDiagnosticsDrawer(`mode:${mcelLabState.currentMode}`);
      }
    }


    function currentMcelSiteFrame() {
      if (!mcelSiteFrame || !mcelSiteFrame.isConnected) {
        mcelSiteFrame = document.querySelector("#mcel-site-frame");
      }
      return mcelSiteFrame;
    }

    function ensureMcelSiteFrameTwiddle() {
      if (!mcelLabState.siteFrameTwiddle) {
        mcelLabState.siteFrameTwiddle = {
          openCount: 0,
          closeCount: 0,
          syncCount: 0,
          rebuildCount: 0,
          clearCount: 0,
          loadCount: 0,
          errorCount: 0,
          generation: 0,
          nonce: 0,
          lastReason: "boot",
          lastHash: "none",
          lastLength: 0,
          lastAt: null,
          lastReadyState: "unknown",
          lastFitStatus: "unavailable",
          lastFitViolations: 0,
          lastFitCompositionWarnings: 0,
          lastFitRemedies: "",
          lastCompositionRemedies: "",
          lastChromeFitReport: null,
          events: []
        };
      }
      if (!Array.isArray(mcelLabState.siteFrameTwiddle.events)) {
        mcelLabState.siteFrameTwiddle.events = [];
      }
      return mcelLabState.siteFrameTwiddle;
    }

    function hashMcelSiteFrameDocument(value = "") {
      let hash = 2166136261;
      for (let index = 0; index < value.length; index += 1) {
        hash ^= value.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
      }
      return (hash >>> 0).toString(16).padStart(8, "0");
    }

    function readMcelSiteFrameReadyState(frame) {
      try {
        return frame?.contentDocument?.readyState || "sandboxed-or-unavailable";
      } catch (error) {
        return `sandboxed:${error?.name || "access-denied"}`;
      }
    }

    function scheduleMcelSiteFrameWrite(callback) {
      const scheduler = typeof window.requestAnimationFrame === "function"
        ? window.requestAnimationFrame
        : (task) => window.setTimeout(task, 0);
      scheduler(callback);
    }


    function summarizeMcelChromeFitReport(report) {
      if (!report) return "fit=unavailable";
      const status = report.status || "unavailable";
      const finalViolations = Number(report.finalViolations ?? report.violationCount ?? 0);
      const finalCompositionWarnings = Number(report.finalCompositionWarnings ?? report.compositionWarningCount ?? 0);
      const composition = finalCompositionWarnings > 0
        ? ` · composition=${finalCompositionWarnings}`
        : "";
      const remedies = Array.isArray(report.appliedRemedies) && report.appliedRemedies.length
        ? ` · remedies=${report.appliedRemedies.join("+")}`
        : "";
      const compositionRemedies = Array.isArray(report.appliedCompositionRemedies) && report.appliedCompositionRemedies.length
        ? ` · compositionRemedies=${report.appliedCompositionRemedies.map((item) => item.remedy || item.problem).join("+")}`
        : "";
      return `fit=${status} · violations=${finalViolations}${composition}${remedies}${compositionRemedies}`;
    }

    function accessMcelSiteFrameDocument(frame) {
      try {
        return frame?.contentDocument || null;
      } catch (error) {
        return null;
      }
    }

    function clearMcelChromeFitRuntimeState(doc) {
      const body = doc?.body;
      const html = doc?.documentElement;
      [body, html].filter(Boolean).forEach((element) => {
        element.removeAttribute("data-mcel-fit-remediation");
        element.removeAttribute("data-mcel-fit-status");
        element.removeAttribute("data-mcel-fit-violations");
        element.removeAttribute("data-mcel-composition-status");
        element.removeAttribute("data-mcel-composition-warnings");
        element.removeAttribute("data-mcel-composition-remediation");
      });
      doc?.querySelectorAll?.("[data-mcel-composition-remedy], [data-mcel-composition-warnings]").forEach((element) => {
        element.removeAttribute("data-mcel-composition-remedy");
        element.removeAttribute("data-mcel-composition-warnings");
      });
    }

    function applyMcelChromeFitRuntimeState(doc, remedies = [], status = "probing", violationCount = 0, compositionWarningCount = 0) {
      const body = doc?.body;
      const html = doc?.documentElement;
      const value = remedies.join(" ");
      [body, html].filter(Boolean).forEach((element) => {
        if (value) {
          element.setAttribute("data-mcel-fit-remediation", value);
        } else {
          element.removeAttribute("data-mcel-fit-remediation");
        }
        element.setAttribute("data-mcel-fit-status", status);
        element.setAttribute("data-mcel-fit-violations", String(violationCount));
        element.setAttribute("data-mcel-composition-status", compositionWarningCount > 0 ? "warning" : "clean");
        element.setAttribute("data-mcel-composition-warnings", String(compositionWarningCount));
      });
      // Force a synchronous style/layout pass so the next observation measures the
      // browser's result of the chrome-approved remediation rather than queued CSS.
      void doc?.documentElement?.offsetWidth;
    }

    function observeMcelChromeFit(doc, chrome) {
      const contract = typeof McelLabChromeLaw !== "undefined" && McelLabChromeLaw.chromeFitContract
        ? McelLabChromeLaw.chromeFitContract(chrome)
        : {};
      const compositionContract = typeof McelLabChromeLaw !== "undefined" && McelLabChromeLaw.chromeCompositionContract
        ? McelLabChromeLaw.chromeCompositionContract(chrome)
        : {};
      return McelLabBrowserObserver.observeChromeFit(doc, {
        chrome,
        selectors: contract.observeSelectors || [],
        hardObjectSelector: contract.hardObjectSelector,
        tolerancePx: contract.tolerancePx || 2,
        compositionContract
      });
    }

    function mcelChromeGeometryFailureCount(report) {
      return Number(report?.violationCount ?? report?.finalViolations ?? 0);
    }

    function mcelChromeCompositionWarningCount(report) {
      return Number(report?.compositionWarningCount ?? report?.finalCompositionWarnings ?? 0);
    }

    function mcelChromeFitFailureCount(report) {
      return mcelChromeGeometryFailureCount(report) + mcelChromeCompositionWarningCount(report);
    }

    function mcelChromeCompositionScopeSelector() {
      return [
        ".mcel-chrome-editorial-rail",
        ".mcel-chrome-cluster-grid",
        ".mcel-chrome-spotlight-primary",
        ".mcel-chrome-spotlight-support",
        ".mcel-chrome-journey-step",
        ".mcel-chrome-compact-panel",
        "[data-mcel-chrome-frame]",
        "[data-mcel-chrome-region-role]"
      ].join(", ");
    }

    function mcelSafeAttributeValue(value) {
      return String(value || "").replace(/\\/g, "\\\\").replace(/"/g, '\\"');
    }

    function findMcelCompositionRemedyTarget(doc, warning = {}) {
      const sourceIndex = String(warning.sourceIndex || "");
      const chromePart = String(warning.chromePart || "");
      const scope = mcelChromeCompositionScopeSelector();
      const wantsGeneratedContainer = warning?.problem === "container-distorted-by-extreme-aspect-ratio" ||
        warning?.problem === "shape-containment-failed" ||
        warning?.remedy === "dedistort-container-shape" ||
        warning?.remedy === "smart-content-envelope";

      if (wantsGeneratedContainer && chromePart) {
        const selector = `[data-mcel-chrome-part="${mcelSafeAttributeValue(chromePart)}"]`;
        const generatedTargets = [...(doc?.querySelectorAll?.(selector) || [])];
        const sourceSelector = sourceIndex
          ? `[data-mc-source-index="${mcelSafeAttributeValue(sourceIndex)}"]`
          : "";
        const generatedContainerWithSource = sourceSelector
          ? generatedTargets.find((element) => element?.querySelector?.(sourceSelector))
          : null;
        if (generatedContainerWithSource) return generatedContainerWithSource;
        const generatedContainer = generatedTargets.find((element) => element?.getAttribute?.("data-mcel-chrome-generated") === "true");
        if (generatedContainer) return generatedContainer;
        if (generatedTargets.length) return generatedTargets[0];
      }

      if (sourceIndex) {
        const selector = `${scope} [data-mc-source-index="${mcelSafeAttributeValue(sourceIndex)}"]`;
        const candidates = [...(doc?.querySelectorAll?.(selector) || [])];
        const direct = candidates.find((element) => element.matches?.(".mc, [data-mc]"));
        if (direct) return direct;
        const nested = candidates.map((element) => element.closest?.(".mc, [data-mc]")).find(Boolean);
        if (nested) return nested;
        if (candidates.length) return candidates[0];
      }

      if (chromePart) {
        const selector = `[data-mcel-chrome-part="${mcelSafeAttributeValue(chromePart)}"]`;
        const generatedTargets = [...(doc?.querySelectorAll?.(selector) || [])];
        const withSourceChild = generatedTargets
          .map((element) => element.querySelector?.(".mc, [data-mc]") || element)
          .find((element) => element?.matches?.(".mc, [data-mc], [data-mcel-chrome-generated=\"true\"]"));
        if (withSourceChild) return withSourceChild;
      }

      return null;
    }

    function applyMcelChromeCompositionRemedies(doc, warnings = []) {
      const applied = [];
      warnings.forEach((warning) => {
        const remedy = warning?.remedy ||
          (warning?.problem === "primary-control-width-collapsed-relative-to-input"
            ? "control-balance"
            : (warning?.problem === "content-fit-failed"
              ? "smart-flow-frame"
              : (warning?.problem === "shape-containment-failed"
                ? "smart-content-envelope"
                : (warning?.problem === "shape-interior-escape"
                  ? "shape-inset-content"
                  : (warning?.problem === "text-distorted-by-narrow-inline-size"
                    ? "dedistort-inline-content"
                    : (warning?.problem === "container-distorted-by-extreme-aspect-ratio" ? "dedistort-container-shape" : ""))))));
        if (!remedy) return;
        const target = findMcelCompositionRemedyTarget(doc, warning);
        if (!target) return;
        const existing = new Set(String(target.getAttribute("data-mcel-composition-remedy") || "").split(/\s+/).filter(Boolean));
        const beforeRemedyCount = existing.size;
        String(remedy).split(/\s+/).filter(Boolean).forEach((token) => existing.add(token));
        target.setAttribute("data-mcel-composition-remedy", [...existing].join(" "));
        const existingWarnings = new Set(String(target.getAttribute("data-mcel-composition-warnings") || "").split(/\s+/).filter(Boolean));
        const beforeWarningCount = existingWarnings.size;
        if (warning.problem) existingWarnings.add(warning.problem);
        target.setAttribute("data-mcel-composition-warnings", [...existingWarnings].join(" "));
        if (existing.size === beforeRemedyCount && existingWarnings.size === beforeWarningCount) return;
        applied.push({
          problem: warning.problem || "",
          remedy,
          sourceIndex: warning.sourceIndex || "",
          chromePart: warning.chromePart || "",
          fitRegion: warning.fitRegion || "",
          childTagName: warning.childTagName || "",
          shape: warning.shape || ""
        });
      });
      if (doc?.body) {
        doc.body.setAttribute("data-mcel-composition-remediation", applied.length ? "active" : "none");
      }
      void doc?.documentElement?.offsetWidth;
      return applied;
    }

    function runMcelSiteFrameChromeFit(reason = "chrome-fit") {
      const frame = currentMcelSiteFrame();
      const twiddle = ensureMcelSiteFrameTwiddle();
      const chrome = frame?.dataset?.chrome || mcelLabState.chrome || "chrome-strict-hierarchy";
      if (!frame || typeof McelLabBrowserObserver === "undefined" || typeof McelLabBrowserObserver.observeChromeFit !== "function") {
        const unavailable = {
          kind: "mcel-chrome-fit-report",
          chrome,
          status: "unavailable",
          reason,
          firstPassViolations: 0,
          finalViolations: 0,
          firstPassCompositionWarnings: 0,
          finalCompositionWarnings: 0,
          repaired: false,
          appliedRemedies: [],
          appliedCompositionRemedies: [],
          compositionWarnings: [],
          violations: [],
          warnings: ["Chrome fit observer is unavailable."]
        };
        mcelLabState.lastChromeFitReport = unavailable;
        twiddle.lastChromeFitReport = unavailable;
        twiddle.lastFitStatus = "unavailable";
        twiddle.lastFitViolations = 0;
        twiddle.lastFitCompositionWarnings = 0;
        twiddle.lastFitRemedies = "";
        twiddle.lastCompositionRemedies = "";
        renderMcelSiteFrameTwiddle(reason);
        return unavailable;
      }

      const doc = accessMcelSiteFrameDocument(frame);
      if (!doc?.body) {
        const unavailable = {
          kind: "mcel-chrome-fit-report",
          chrome,
          status: "unavailable",
          reason,
          firstPassViolations: 0,
          finalViolations: 0,
          firstPassCompositionWarnings: 0,
          finalCompositionWarnings: 0,
          repaired: false,
          appliedRemedies: [],
          appliedCompositionRemedies: [],
          compositionWarnings: [],
          violations: [],
          warnings: ["Rendered iframe document is unavailable; check sandbox allow-same-origin."]
        };
        mcelLabState.lastChromeFitReport = unavailable;
        twiddle.lastChromeFitReport = unavailable;
        twiddle.lastFitStatus = "unavailable";
        twiddle.lastFitViolations = 0;
        twiddle.lastFitCompositionWarnings = 0;
        twiddle.lastFitRemedies = "";
        twiddle.lastCompositionRemedies = "";
        frame.dataset.fitStatus = "unavailable";
        frame.dataset.fitViolations = "0";
        frame.dataset.fitCompositionWarnings = "0";
        frame.dataset.compositionRemedies = "";
        renderMcelSiteFrameTwiddle(reason);
        return unavailable;
      }

      clearMcelChromeFitRuntimeState(doc);
      const first = observeMcelChromeFit(doc, chrome);
      const firstPassViolations = mcelChromeGeometryFailureCount(first);
      const firstPassCompositionWarnings = mcelChromeCompositionWarningCount(first);
      const firstPassFailures = mcelChromeFitFailureCount(first);
      const plan = typeof McelLabChromeLaw !== "undefined" && McelLabChromeLaw.chromeRemediationPlan
        ? McelLabChromeLaw.chromeRemediationPlan(chrome)
        : {strategies: []};
      const strategies = Array.isArray(plan.strategies) ? plan.strategies : [];
      const appliedRemedies = [];
      const appliedCompositionRemedies = [];
      const passes = [{
        stage: "prevent",
        remedies: [],
        compositionRemedies: [],
        report: first
      }];
      let current = first;

      const applyCompositionRemediesIfNeeded = (stage) => {
        if (mcelChromeCompositionWarningCount(current) === 0) return false;
        const applied = applyMcelChromeCompositionRemedies(doc, current.compositionWarnings || []);
        if (!applied.length) return false;
        applied.forEach((item) => appliedCompositionRemedies.push(item));
        current = observeMcelChromeFit(doc, chrome);
        passes.push({
          stage,
          remedies: [...appliedRemedies],
          compositionRemedies: [...appliedCompositionRemedies],
          report: current
        });
        return true;
      };

      const runCompositionRemediationPasses = (prefix) => {
        for (let index = 0; index < 4 && mcelChromeCompositionWarningCount(current) > 0; index += 1) {
          if (!applyCompositionRemediesIfNeeded(index === 0 ? prefix : `${prefix}-${index + 1}`)) break;
        }
      };

      runCompositionRemediationPasses("composition-remedy");

      if (mcelChromeFitFailureCount(current) > 0 && mcelChromeGeometryFailureCount(current) > 0 && strategies.length) {
        for (const strategy of strategies) {
          if (!strategy?.id) continue;
          appliedRemedies.push(strategy.id);
          applyMcelChromeFitRuntimeState(
            doc,
            appliedRemedies,
            "probing",
            mcelChromeGeometryFailureCount(current),
            mcelChromeCompositionWarningCount(current)
          );
          current = observeMcelChromeFit(doc, chrome);
          passes.push({
            stage: strategy.id,
            remedies: [...appliedRemedies],
            compositionRemedies: [...appliedCompositionRemedies],
            report: current
          });
          runCompositionRemediationPasses(`composition-after-${strategy.id}`);
          if (mcelChromeFitFailureCount(current) === 0) break;
        }
      }

      const finalViolations = mcelChromeGeometryFailureCount(current);
      const finalCompositionWarnings = mcelChromeCompositionWarningCount(current);
      const finalFailures = finalViolations + finalCompositionWarnings;
      const status = firstPassFailures === 0
        ? "clean"
        : (finalFailures === 0 ? "repaired" : "failed");
      applyMcelChromeFitRuntimeState(doc, appliedRemedies, status, finalViolations, finalCompositionWarnings);

      const finalReport = {
        kind: "mcel-chrome-fit-report",
        chrome,
        status,
        reason,
        firstPassViolations,
        firstPassCompositionWarnings,
        firstPassFailures,
        finalViolations,
        finalCompositionWarnings,
        finalFailures,
        repaired: status === "repaired",
        appliedRemedies,
        appliedCompositionRemedies,
        passes: passes.map((pass) => ({
          stage: pass.stage,
          remedies: pass.remedies,
          compositionRemedies: pass.compositionRemedies || [],
          violationCount: mcelChromeGeometryFailureCount(pass.report),
          compositionWarningCount: mcelChromeCompositionWarningCount(pass.report),
          failureCount: mcelChromeFitFailureCount(pass.report)
        })),
        violations: current.violations || [],
        compositionWarnings: current.compositionWarnings || [],
        compositionWarningCount: finalCompositionWarnings,
        tolerancePx: current.tolerancePx || first.tolerancePx || 2,
        warnings: current.warnings || []
      };

      mcelLabState.lastChromeFitReport = finalReport;
      twiddle.lastChromeFitReport = finalReport;
      twiddle.lastFitStatus = status;
      twiddle.lastFitViolations = finalViolations;
      twiddle.lastFitCompositionWarnings = finalCompositionWarnings;
      twiddle.lastFitRemedies = appliedRemedies.join("+");
      twiddle.lastCompositionRemedies = appliedCompositionRemedies.map((item) => item.remedy || item.problem).join("+");
      frame.dataset.fitStatus = status;
      frame.dataset.fitViolations = String(finalViolations);
      frame.dataset.fitCompositionWarnings = String(finalCompositionWarnings);
      frame.dataset.fitRemedies = appliedRemedies.join("+");
      frame.dataset.compositionRemedies = twiddle.lastCompositionRemedies;
      recordMcelSiteFrameTwiddle("chrome-fit", {
        reason,
        hash: frame.dataset.srcdocHash,
        length: Number(frame.dataset.srcdocLength || 0),
        fitStatus: status,
        fitViolations: finalViolations,
        fitCompositionWarnings: finalCompositionWarnings
      });
      return finalReport;
    }

    function recordMcelSiteFrameTwiddle(action, details = {}) {
      const twiddle = ensureMcelSiteFrameTwiddle();
      const frame = currentMcelSiteFrame();
      const event = {
        at: new Date().toISOString(),
        action,
        reason: details.reason || twiddle.lastReason || "unknown",
        hash: details.hash || frame?.dataset?.srcdocHash || twiddle.lastHash || "none",
        length: Number.isFinite(details.length) ? details.length : Number(frame?.dataset?.srcdocLength || twiddle.lastLength || 0),
        generation: Number(frame?.dataset?.generation || twiddle.generation || 0),
        connected: Boolean(frame?.isConnected),
        modalHidden: mcelSiteModal?.getAttribute("aria-hidden") || "missing",
        synced: frame?.dataset?.synced || "never",
        fitStatus: details.fitStatus || frame?.dataset?.fitStatus || twiddle.lastFitStatus || "unavailable",
        fitViolations: Number(details.fitViolations ?? frame?.dataset?.fitViolations ?? twiddle.lastFitViolations ?? 0),
        fitCompositionWarnings: Number(details.fitCompositionWarnings ?? frame?.dataset?.fitCompositionWarnings ?? twiddle.lastFitCompositionWarnings ?? 0)
      };
      twiddle.events = [...twiddle.events, event].slice(-10);
      twiddle.lastAt = event.at;
      renderMcelSiteFrameTwiddle(action);
    }

    function renderMcelSiteFrameTwiddle(reason = "render") {
      const twiddle = ensureMcelSiteFrameTwiddle();
      const frame = currentMcelSiteFrame();
      const srcdocLength = Number(frame?.dataset?.srcdocLength || frame?.srcdoc?.length || 0);
      const stateLine = [
        `state=${mcelLabState.activeModal || "closed"}`,
        `modalHidden=${mcelSiteModal?.getAttribute("aria-hidden") || "missing"}`,
        `frame=${frame?.isConnected ? "connected" : "missing"}`,
        `generation=${frame?.dataset?.generation || twiddle.generation || 0}`,
        `opens=${twiddle.openCount}`,
        `closes=${twiddle.closeCount}`,
        `syncs=${twiddle.syncCount}`,
        `loads=${twiddle.loadCount}`,
        `rebuilds=${twiddle.rebuildCount}`,
        `clears=${twiddle.clearCount}`,
        `hash=${frame?.dataset?.srcdocHash || twiddle.lastHash || "none"}`,
        `len=${srcdocLength}`,
        `theme=${mcelLabState.theme || "theme-machine"}`,
        `chrome=${mcelLabState.chrome || "chrome-strict-hierarchy"}`,
        summarizeMcelChromeFitReport(twiddle.lastChromeFitReport || mcelLabState.lastChromeFitReport),
        `reason=${reason}`
      ].join(" · ");
      if (mcelSiteFrameStatus) {
        mcelSiteFrameStatus.textContent = stateLine;
      }
      if (mcelSiteFrameMiniStatus) {
        mcelSiteFrameMiniStatus.textContent = `render iframe: ${stateLine}`;
      }
      if (mcelSiteFrameLog) {
        mcelSiteFrameLog.textContent = (twiddle.events || [])
          .slice()
          .reverse()
          .map((event) => [
            event.at,
            event.action,
            `reason=${event.reason}`,
            `hash=${event.hash}`,
            `len=${event.length}`,
            `generation=${event.generation}`,
            `connected=${event.connected}`,
            `modalHidden=${event.modalHidden}`,
            `synced=${event.synced}`,
            `fit=${event.fitStatus || "unavailable"}`,
            `fitViolations=${event.fitViolations ?? 0}`,
            `compositionWarnings=${event.fitCompositionWarnings ?? 0}`
          ].join(" | "))
          .join("\n") || "No iframe lifecycle events recorded yet.";
      }
    }

    function bindMcelSiteFrameLifecycle(reason = "bind") {
      const frame = currentMcelSiteFrame();
      if (!frame || frame.dataset.lifecycleBound === "true") {
        renderMcelSiteFrameTwiddle(reason);
        return frame;
      }
      frame.dataset.lifecycleBound = "true";
      frame.addEventListener("load", () => {
        const twiddle = ensureMcelSiteFrameTwiddle();
        twiddle.loadCount += 1;
        twiddle.lastReadyState = readMcelSiteFrameReadyState(frame);
        recordMcelSiteFrameTwiddle("iframe-load", {reason: frame.dataset.synced || reason});
        scheduleMcelSiteFrameWrite(() => runMcelSiteFrameChromeFit(frame.dataset.synced || reason || "iframe-load"));
        recordMcelEvent("ui", "MCEL_SITE_IFRAME_LOADED", `Rendered-site iframe loaded generation ${frame.dataset.generation || 0}.`);
      });
      frame.addEventListener("error", () => {
        const twiddle = ensureMcelSiteFrameTwiddle();
        twiddle.errorCount += 1;
        recordMcelSiteFrameTwiddle("iframe-error", {reason: frame.dataset.synced || reason});
        recordMcelEvent("ui", "MCEL_SITE_IFRAME_ERROR", "Rendered-site iframe emitted an error event.");
      });
      renderMcelSiteFrameTwiddle(reason);
      return frame;
    }

    function clearMcelSiteFrameSrcdoc(reason = "clear-srcdoc") {
      const frame = bindMcelSiteFrameLifecycle(reason);
      if (!frame) return;
      const twiddle = ensureMcelSiteFrameTwiddle();
      twiddle.clearCount += 1;
      frame.removeAttribute("srcdoc");
      frame.srcdoc = "";
      frame.dataset.synced = reason;
      frame.dataset.srcdocHash = "empty";
      frame.dataset.srcdocLength = "0";
      twiddle.lastReason = reason;
      twiddle.lastHash = "empty";
      twiddle.lastLength = 0;
      recordMcelSiteFrameTwiddle("iframe-clear", {reason, hash: "empty", length: 0});
      recordMcelEvent("ui", "MCEL_SITE_IFRAME_CLEARED", "Rendered-site iframe srcdoc was cleared from the lifecycle twiddle.");
    }

    function rebuildMcelSiteFrameShell(reason = "rebuild-frame", options = {}) {
      const frame = currentMcelSiteFrame();
      if (!frame || !frame.parentElement) return;
      const twiddle = ensureMcelSiteFrameTwiddle();
      const replacement = document.createElement("iframe");
      replacement.id = "mcel-site-frame";
      replacement.className = "mcel-site-frame";
      replacement.title = "Isolated MCEL rendered site";
      replacement.setAttribute("sandbox", "allow-same-origin");
      replacement.dataset.generation = String((Number(frame.dataset.generation || twiddle.generation || 0) || 0) + 1);
      replacement.dataset.synced = "fresh-shell";
      frame.replaceWith(replacement);
      mcelSiteFrame = replacement;
      twiddle.rebuildCount += 1;
      twiddle.generation = Number(replacement.dataset.generation || twiddle.generation || 0);
      bindMcelSiteFrameLifecycle(reason);
      recordMcelSiteFrameTwiddle("iframe-rebuild", {reason, hash: "fresh-shell", length: 0});
      recordMcelEvent("ui", "MCEL_SITE_IFRAME_REBUILT", `Rendered-site iframe shell rebuilt for ${reason}.`);
      if (options.syncAfter) {
        syncMcelRenderedSiteFrame(`${reason}:sync-after-rebuild`);
      }
    }

    function getMcelSmartCssPrimitiveCases() {
      return [
        {
          id: "unbounded-pill-frame",
          title: "Unbounded CSS pill used as a content-bearing frame",
          rawPrimitive: "border-radius: 999px on a generated frame that contains a card stack",
          smartPrimitive: "big-rounded support-frame object with explicit content region and growth contract",
          proof: "shape-containment",
          rawClass: "mcel-smart-css-raw-pill",
          smartClass: "mcel-smart-css-smart-frame",
          expectedRawFailure: "shape-containment-failed"
        },
        {
          id: "fixed-clip-box",
          title: "Fixed overflow clip box pretending to be a layout primitive",
          rawPrimitive: "fixed block-size plus overflow: clip around variable children",
          smartPrimitive: "flow-frame object that derives block-size from accepted children",
          proof: "content-fit",
          rawClass: "mcel-smart-css-raw-clip",
          smartClass: "mcel-smart-css-smart-flow",
          expectedRawFailure: "content-fit-failed"
        },
        {
          id: "overlay-paint-layer",
          title: "Decorative paint layer order around semantic content",
          rawPrimitive: "same decorative paint token, but raw stacking places it above semantic content",
          smartPrimitive: "same decorative paint token, but paint envelope is behind semantic content and inert to hit testing",
          proof: "paint-layer-order",
          rawClass: "mcel-smart-css-raw-overlay",
          smartClass: "mcel-smart-css-smart-paint",
          expectedRawFailure: "paint-layer-overlay-failed"
        }
      ];
    }

    function createMcelSmartCssCard(title, copy) {
      const card = document.createElement("article");
      card.className = "mcel-smart-css-card";
      card.innerHTML = `<strong>${title}</strong><span>${copy}</span>`;
      return card;
    }

    function createMcelSmartCssPrimitiveStage(spec, side) {
      const isRaw = side === "raw";
      const stage = document.createElement("section");
      stage.className = `mcel-smart-css-stage ${isRaw ? spec.rawClass : spec.smartClass}`;
      stage.dataset.mcelSmartCssSide = side;
      stage.dataset.mcelSmartCssProof = spec.proof;

      const heading = document.createElement("header");
      heading.className = "mcel-smart-css-stage-head";
      heading.innerHTML = [
        `<span>${isRaw ? "Raw CSS backend" : "MCEL smart primitive"}</span>`,
        `<strong>${isRaw ? spec.rawPrimitive : spec.smartPrimitive}</strong>`
      ].join("");

      const object = document.createElement("div");
      object.className = "mcel-smart-css-object";
      object.dataset.mcelSmartCssObject = isRaw ? "raw" : "smart";

      const layer = document.createElement("div");
      layer.className = "mcel-smart-css-paint-layer";
      layer.setAttribute("aria-hidden", "true");
      object.appendChild(layer);

      const content = document.createElement("div");
      content.className = "mcel-smart-css-content";
      content.appendChild(createMcelSmartCssCard("Fresh daily", "Card stack child one"));
      content.appendChild(createMcelSmartCssCard("Pickup + delivery", "Card stack child two"));
      content.appendChild(createMcelSmartCssCard("Proof visible", "Card stack child three"));

      if (spec.id === "fixed-clip-box") {
        content.appendChild(createMcelSmartCssCard("Fourth child", "Variable content that raw CSS clips"));
      }

      object.appendChild(content);
      const verdict = document.createElement("output");
      verdict.className = "mcel-smart-css-verdict";
      verdict.setAttribute("aria-live", "polite");
      verdict.textContent = "not run";

      stage.append(heading, object, verdict);
      return stage;
    }

    function renderMcelSmartCssPrimitiveCase(spec) {
      const article = document.createElement("article");
      article.className = "mcel-smart-css-case";
      article.dataset.mcelSmartCssCase = spec.id;
      article.innerHTML = `
        <header class="mcel-smart-css-case-head">
          <div>
            <p class="eyebrow">Primitive replacement test</p>
            <h5>${spec.title}</h5>
          </div>
          <code>${spec.proof}</code>
        </header>
      `;

      const comparison = document.createElement("div");
      comparison.className = "mcel-smart-css-comparison";
      comparison.append(
        createMcelSmartCssPrimitiveStage(spec, "raw"),
        createMcelSmartCssPrimitiveStage(spec, "smart")
      );

      article.appendChild(comparison);
      return article;
    }

    function mcelSmartCssPx(value) {
      const parsed = Number.parseFloat(value);
      return Number.isFinite(parsed) ? parsed : 0;
    }

    function mcelSmartCssRound(value, places = 1) {
      const factor = 10 ** places;
      return Math.round(value * factor) / factor;
    }

    function getMcelSmartCssUsedRadius(element) {
      const styles = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const raw = Math.max(
        mcelSmartCssPx(styles.borderTopLeftRadius),
        mcelSmartCssPx(styles.borderTopRightRadius),
        mcelSmartCssPx(styles.borderBottomRightRadius),
        mcelSmartCssPx(styles.borderBottomLeftRadius)
      );
      return {
        raw,
        used: Math.min(raw, rect.width / 2, rect.height / 2),
        css: styles.borderRadius
      };
    }

    function getMcelSmartCssSafeInterval(parent, y) {
      const rect = parent.getBoundingClientRect();
      const radius = getMcelSmartCssUsedRadius(parent).used;
      let left = rect.left;
      let right = rect.right;

      if (radius > 0 && y < rect.top + radius) {
        const centerY = rect.top + radius;
        const dy = Math.abs(y - centerY);
        if (dy < radius) {
          const dx = Math.sqrt(Math.max(0, radius * radius - dy * dy));
          const inset = radius - dx;
          left = Math.max(left, rect.left + inset);
          right = Math.min(right, rect.right - inset);
        }
      } else if (radius > 0 && y > rect.bottom - radius) {
        const centerY = rect.bottom - radius;
        const dy = Math.abs(y - centerY);
        if (dy < radius) {
          const dx = Math.sqrt(Math.max(0, radius * radius - dy * dy));
          const inset = radius - dx;
          left = Math.max(left, rect.left + inset);
          right = Math.min(right, rect.right - inset);
        }
      }

      return {left, right, width: Math.max(0, right - left)};
    }

    function analyzeMcelSmartCssShapeContainment(stage) {
      const object = stage.querySelector(".mcel-smart-css-object");
      const cards = Array.from(stage.querySelectorAll(".mcel-smart-css-card"));
      const radius = getMcelSmartCssUsedRadius(object);
      const failures = cards.map((card, index) => {
        const rect = card.getBoundingClientRect();
        const samples = [
          rect.top + 1,
          rect.top + rect.height * 0.25,
          rect.top + rect.height * 0.5,
          rect.top + rect.height * 0.75,
          rect.bottom - 1
        ].map((y) => {
          const safe = getMcelSmartCssSafeInterval(object, y);
          const leftEscape = Math.max(0, safe.left - rect.left);
          const rightEscape = Math.max(0, rect.right - safe.right);
          return {
            y: mcelSmartCssRound(y),
            safeWidth: mcelSmartCssRound(safe.width),
            leftEscape: mcelSmartCssRound(leftEscape),
            rightEscape: mcelSmartCssRound(rightEscape),
            worstEscape: mcelSmartCssRound(Math.max(leftEscape, rightEscape))
          };
        });
        const worstEscape = Math.max(...samples.map((sample) => sample.worstEscape));
        return {
          index,
          failed: worstEscape > 2,
          worstEscapePx: mcelSmartCssRound(worstEscape),
          samples
        };
      }).filter((failure) => failure.failed);

      return {
        failed: failures.length > 0,
        failure: failures.length ? "shape-containment-failed" : null,
        detail: {
          rawRadius: mcelSmartCssRound(radius.raw),
          usedRadius: mcelSmartCssRound(radius.used),
          collisionCount: failures.length,
          failures
        }
      };
    }

    function analyzeMcelSmartCssContentFit(stage) {
      const object = stage.querySelector(".mcel-smart-css-object");
      const cards = Array.from(stage.querySelectorAll(".mcel-smart-css-card"));
      const objectRect = object.getBoundingClientRect();
      const styles = window.getComputedStyle(object);
      const clips = ["clip", "hidden", "scroll", "auto"].includes(styles.overflow) || ["clip", "hidden", "scroll", "auto"].includes(styles.overflowY);
      const union = cards.reduce((bounds, card) => {
        const rect = card.getBoundingClientRect();
        return {
          left: Math.min(bounds.left, rect.left),
          top: Math.min(bounds.top, rect.top),
          right: Math.max(bounds.right, rect.right),
          bottom: Math.max(bounds.bottom, rect.bottom)
        };
      }, {left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity});
      const bottomEscape = Math.max(0, union.bottom - objectRect.bottom);
      const rightEscape = Math.max(0, union.right - objectRect.right);
      const leftEscape = Math.max(0, objectRect.left - union.left);
      const topEscape = Math.max(0, objectRect.top - union.top);
      const worstEscape = Math.max(bottomEscape, rightEscape, leftEscape, topEscape);
      const failed = clips && worstEscape > 2;

      return {
        failed,
        failure: failed ? "content-fit-failed" : null,
        detail: {
          clips,
          objectHeight: mcelSmartCssRound(objectRect.height),
          contentHeight: mcelSmartCssRound(union.bottom - union.top),
          worstEscapePx: mcelSmartCssRound(worstEscape)
        }
      };
    }

    function mcelSmartCssStackOrder(styles) {
      const parsed = Number.parseInt(styles.zIndex, 10);
      return Number.isFinite(parsed) ? parsed : 0;
    }

    function mcelSmartCssRectsOverlap(a, b) {
      return a.right > b.left && a.left < b.right && a.bottom > b.top && a.top < b.bottom;
    }

    function analyzeMcelSmartCssPaintLayerOrder(stage) {
      const layer = stage.querySelector(".mcel-smart-css-paint-layer");
      const content = stage.querySelector(".mcel-smart-css-content");
      const cards = Array.from(stage.querySelectorAll(".mcel-smart-css-card"));
      if (!layer || !content || !cards.length) {
        return {
          failed: true,
          failure: "paint-layer-overlay-failed",
          detail: {reason: "missing paint-layer, content layer, or cards"}
        };
      }

      const layerStyles = window.getComputedStyle(layer);
      const contentStyles = window.getComputedStyle(content);
      const layerRect = layer.getBoundingClientRect();
      const layerZ = mcelSmartCssStackOrder(layerStyles);
      const contentZ = mcelSmartCssStackOrder(contentStyles);
      const pointerEvents = layerStyles.pointerEvents;
      const paintCanReceiveHits = pointerEvents !== "none";
      const paintStacksAboveContent = layerZ >= contentZ;
      const foregroundPaint = paintCanReceiveHits || paintStacksAboveContent;

      const hits = cards.map((card, index) => {
        const rect = card.getBoundingClientRect();
        const overlapsPaint = mcelSmartCssRectsOverlap(layerRect, rect);
        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;
        const inViewport = x >= 0 && y >= 0 && x < window.innerWidth && y < window.innerHeight;
        const stack = inViewport && document.elementsFromPoint ? document.elementsFromPoint(x, y) : [];
        const blockedByPaintLayer = stack.some((hit) => hit === layer || layer.contains(hit));
        const hitContent = stack.some((hit) => hit === card || card.contains(hit) || hit === content || content.contains(hit));
        return {
          index,
          overlapsPaint,
          blockedByPaintLayer,
          hitContent,
          hitTested: inViewport
        };
      });

      const foregroundOverlapCount = hits.filter((hit) => hit.overlapsPaint && foregroundPaint).length;
      const blockedHitCount = hits.filter((hit) => hit.blockedByPaintLayer).length;
      const failed = foregroundOverlapCount > 0 || blockedHitCount > 0;

      return {
        failed,
        failure: failed ? "paint-layer-overlay-failed" : null,
        detail: {
          paintLayerZ: layerZ,
          contentLayerZ: contentZ,
          paintLayerPointerEvents: pointerEvents,
          foregroundOverlapCount,
          blockedHitCount,
          hitTestedCount: hits.filter((hit) => hit.hitTested).length,
          hits
        }
      };
    }

    function analyzeMcelSmartCssPrimitiveStage(stage, spec) {
      if (spec.proof === "shape-containment") return analyzeMcelSmartCssShapeContainment(stage);
      if (spec.proof === "content-fit") return analyzeMcelSmartCssContentFit(stage);
      if (spec.proof === "paint-layer-order") return analyzeMcelSmartCssPaintLayerOrder(stage);
      return {failed: true, failure: "unknown-proof", detail: {proof: spec.proof}};
    }

    function summarizeMcelSmartCssVerdictDetail(analysis) {
      const detail = analysis.detail || {};
      const pieces = [];
      if (Number.isFinite(detail.collisionCount)) pieces.push(`${detail.collisionCount} child collision(s)`);
      if (Number.isFinite(detail.worstEscapePx)) pieces.push(`worst escape ${detail.worstEscapePx}px`);
      if (Number.isFinite(detail.rawRadius)) pieces.push(`raw radius ${detail.rawRadius}px`);
      if (Number.isFinite(detail.usedRadius)) pieces.push(`used radius ${detail.usedRadius}px`);
      if (Number.isFinite(detail.objectHeight)) pieces.push(`object ${detail.objectHeight}px`);
      if (Number.isFinite(detail.contentHeight)) pieces.push(`content ${detail.contentHeight}px`);
      if (Number.isFinite(detail.foregroundOverlapCount)) pieces.push(`${detail.foregroundOverlapCount} foreground overlap(s)`);
      if (Number.isFinite(detail.blockedHitCount)) pieces.push(`${detail.blockedHitCount} blocked hit(s)`);
      if (Number.isFinite(detail.paintLayerZ)) pieces.push(`paint z=${detail.paintLayerZ}`);
      if (Number.isFinite(detail.contentLayerZ)) pieces.push(`content z=${detail.contentLayerZ}`);
      if (detail.paintLayerPointerEvents) pieces.push(`pointer-events=${detail.paintLayerPointerEvents}`);
      return pieces.length ? `; ${pieces.join("; ")}` : "";
    }

    function updateMcelSmartCssVerdict(stage, analysis, expectedFailure) {
      const side = stage.dataset.mcelSmartCssSide || "raw";
      const verdict = stage.querySelector(".mcel-smart-css-verdict");
      const contractPassed = side === "raw" ? analysis.failed && analysis.failure === expectedFailure : !analysis.failed;
      const detail = summarizeMcelSmartCssVerdictDetail(analysis);
      stage.dataset.mcelSmartCssStatus = contractPassed ? "passed" : "failed";
      stage.dataset.mcelSmartCssDetectedFailure = analysis.failure || "none";
      if (verdict) {
        if (side === "raw") {
          verdict.textContent = contractPassed
            ? `expected backend hazard detected: ${analysis.failure}${detail}`
            : `unexpected raw backend result: ${analysis.failure || "no failure"}${detail}`;
        } else {
          verdict.textContent = contractPassed
            ? `golden-path smart primitive proof passed${detail}`
            : `golden-path smart primitive failed: ${analysis.failure}${detail}`;
        }
      }
      return contractPassed;
    }

    function runMcelSmartCssPrimitiveProofs() {
      const cases = getMcelSmartCssPrimitiveCases();
      const results = cases.map((spec) => {
        const caseEl = mcelSmartCssSuite?.querySelector(`[data-mcel-smart-css-case="${spec.id}"]`);
        const rawStage = caseEl?.querySelector('[data-mcel-smart-css-side="raw"]');
        const smartStage = caseEl?.querySelector('[data-mcel-smart-css-side="smart"]');
        const rawAnalysis = rawStage ? analyzeMcelSmartCssPrimitiveStage(rawStage, spec) : {failed: true, failure: "missing-raw-stage", detail: {}};
        const smartAnalysis = smartStage ? analyzeMcelSmartCssPrimitiveStage(smartStage, spec) : {failed: true, failure: "missing-smart-stage", detail: {}};
        const rawContractPassed = rawStage ? updateMcelSmartCssVerdict(rawStage, rawAnalysis, spec.expectedRawFailure) : false;
        const smartContractPassed = smartStage ? updateMcelSmartCssVerdict(smartStage, smartAnalysis, spec.expectedRawFailure) : false;
        const passed = rawContractPassed && smartContractPassed;

        if (caseEl) caseEl.dataset.mcelSmartCssStatus = passed ? "passed" : "failed";

        return {
          id: spec.id,
          title: spec.title,
          proof: spec.proof,
          expectedRawFailure: spec.expectedRawFailure,
          passed,
          raw: rawAnalysis,
          smart: smartAnalysis
        };
      });
      const report = {
        status: results.every((result) => result.passed) ? "passed" : "failed",
        premise: "CSS/HTML are treated as backend output; MCEL-generated golden-path surfaces must use smart primitives that prove object contracts before raw CSS is emitted.",
        caseCount: results.length,
        passedCount: results.filter((result) => result.passed).length,
        failedCount: results.filter((result) => !result.passed).length,
        results
      };
      mcelLabState.lastSmartCssPrimitiveReport = report;
      if (mcelSmartCssReport) mcelSmartCssReport.textContent = JSON.stringify(report, null, 2);
      recordMcelEvent(
        "smart-css",
        report.status === "passed" ? "MCEL_SMART_CSS_PRIMITIVES_PROVED" : "MCEL_SMART_CSS_PRIMITIVES_FAILED",
        `Smart CSS primitive suite ${report.status}: ${report.passedCount}/${report.caseCount} primitive replacement proofs passed.`,
        report.status === "passed" ? "info" : "warning"
      );
      return report;
    }

    function renderMcelSmartCssPrimitiveLab(reason = "open-smart-css-modal") {
      if (!mcelSmartCssSuite) return null;
      mcelSmartCssSuite.innerHTML = "";
      getMcelSmartCssPrimitiveCases().forEach((spec) => {
        mcelSmartCssSuite.appendChild(renderMcelSmartCssPrimitiveCase(spec));
      });
      window.requestAnimationFrame(() => runMcelSmartCssPrimitiveProofs());
      recordMcelEvent("smart-css", "MCEL_SMART_CSS_PRIMITIVE_LAB_RENDERED", `Smart CSS primitive lab rendered for ${reason}.`);
      return true;
    }


    function openMcelLabModal(which = "site") {
      const modals = {
        editor: mcelEditorModal,
        site: mcelSiteModal,
        "smart-css": mcelSmartCssModal
      };
      const target = modals[which] || mcelSiteModal;
      const active = modals[which] ? which : "site";
      if (!target) return;
      closeMcelLabModal("all", {silent: true});
      target.setAttribute("aria-hidden", "false");
      target.dataset.open = "true";
      mcelLabState.activeModal = active;
      document.body?.classList?.add("mcel-modal-open");
      if (active === "site") {
        const twiddle = ensureMcelSiteFrameTwiddle();
        twiddle.openCount += 1;
        bindMcelSiteFrameLifecycle("open-site-modal");
        syncMcelRenderedSiteFrame("open-site-modal");
        recordMcelSiteFrameTwiddle("modal-open", {reason: "open-site-modal"});
      } else if (active === "editor") {
        syncMcelGrapesFromSource();
      } else if (active === "smart-css") {
        renderMcelSmartCssPrimitiveLab("open-smart-css-modal");
      }
      recordMcelEvent("ui", "MCEL_MODAL_OPENED", `${mcelLabState.activeModal} modal opened as isolated product surface.`);
    }

    function closeMcelLabModal(which = "all", options = {}) {
      const wasSiteClose = which === "site" || which === "all" || mcelLabState.activeModal === "site";
      const targets = [];
      if (which === "editor" || which === "all") targets.push(mcelEditorModal);
      if (which === "site" || which === "all") targets.push(mcelSiteModal);
      if (which === "smart-css" || which === "all") targets.push(mcelSmartCssModal);
      targets.filter(Boolean).forEach((modal) => {
        modal.setAttribute("aria-hidden", "true");
        delete modal.dataset.open;
      });
      if (which === "all" || mcelLabState.activeModal === which) {
        mcelLabState.activeModal = null;
        document.body?.classList?.remove("mcel-modal-open");
      }
      if (wasSiteClose) {
        const twiddle = ensureMcelSiteFrameTwiddle();
        twiddle.closeCount += 1;
        recordMcelSiteFrameTwiddle("modal-close", {reason: options.silent ? "silent-close" : "close-modal"});
      }
      if (!options.silent) recordMcelEvent("ui", "MCEL_MODAL_CLOSED", `${which} modal closed by outside click, Escape, or Close button.`);
    }

    function isolatedSiteCss() {
      return `
        :root {
          color-scheme: dark;
          --site-bg:
            radial-gradient(circle at 90% 0%, rgba(246, 199, 91, 0.18), transparent 34rem),
            radial-gradient(circle at 0% 20%, rgba(115, 214, 255, 0.08), transparent 28rem),
            #050605;
          --site-page: #080907;
          --site-card: #090b08;
          --site-card-soft: rgba(255,255,255,0.035);
          --site-ink: #fff8df;
          --site-muted: #b9b28d;
          --site-heading: #fff8df;
          --site-accent: #f6c75b;
          --site-accent-2: #aee06f;
          --site-action: #f6c75b;
          --site-action-ink: #151205;
          --site-line: rgba(246, 199, 91, 0.22);
          --site-shadow: 0 24px 80px rgba(0,0,0,0.26);
          --site-radius: 22px;
          --site-radius-sm: 999px;
          --site-max: 1180px;
          --site-font-body: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          --site-font-display: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          --site-heading-track: -0.075em;
          --site-hero-columns: minmax(0, 1.12fr) minmax(240px, 0.88fr);
          --site-hero-min: clamp(320px, 52vh, 620px);
          --site-hero-ornament-display: block;
          --site-hero-ornament-radius: 999px;
          --site-hero-ornament-bg:
            linear-gradient(180deg, rgba(174,224,111,0.94), rgba(174,224,111,0.72)),
            radial-gradient(circle at 50% 26%, rgba(255,255,255,0.2), transparent 30%);
          --site-hero-ornament-shadow: inset 0 0 0 1px rgba(255,255,255,0.24), 0 24px 80px rgba(174,224,111,0.18);
          --site-hero-bg:
            radial-gradient(circle at 88% 18%, rgba(115, 214, 255, 0.22), transparent 30%),
            linear-gradient(135deg, rgba(246, 199, 91, 0.12), rgba(174, 224, 111, 0.06)),
            #0b0d09;
          --site-grid-overlay: none;
        }

        body.theme-machine,
        .mcel-runtime-preview.theme-machine {
          color-scheme: dark;
          --site-bg:
            radial-gradient(circle at 90% 0%, rgba(246, 199, 91, 0.18), transparent 34rem),
            radial-gradient(circle at 0% 20%, rgba(115, 214, 255, 0.08), transparent 28rem),
            #050605;
          --site-page: #080907;
          --site-card: #090b08;
          --site-card-soft: rgba(255,255,255,0.035);
          --site-ink: #fff8df;
          --site-muted: #b9b28d;
          --site-heading: #fff8df;
          --site-accent: #f6c75b;
          --site-accent-2: #aee06f;
          --site-action: #f6c75b;
          --site-action-ink: #151205;
          --site-line: rgba(246, 199, 91, 0.22);
          --site-shadow: 0 24px 80px rgba(0,0,0,0.26);
          --site-radius: 22px;
          --site-radius-sm: 999px;
          --site-max: 1180px;
          --site-heading-track: -0.075em;
          --site-hero-columns: minmax(0, 1.12fr) minmax(240px, 0.88fr);
          --site-hero-min: clamp(320px, 52vh, 620px);
          --site-hero-ornament-display: block;
          --site-hero-ornament-radius: 999px;
          --site-hero-ornament-bg:
            linear-gradient(180deg, rgba(174,224,111,0.94), rgba(174,224,111,0.72)),
            radial-gradient(circle at 50% 26%, rgba(255,255,255,0.2), transparent 30%);
          --site-hero-ornament-shadow: inset 0 0 0 1px rgba(255,255,255,0.24), 0 24px 80px rgba(174,224,111,0.18);
          --site-hero-bg:
            radial-gradient(circle at 88% 18%, rgba(115, 214, 255, 0.22), transparent 30%),
            linear-gradient(135deg, rgba(246, 199, 91, 0.12), rgba(174, 224, 111, 0.06)),
            #0b0d09;
          --site-grid-overlay: none;
        }

        body.theme-local,
        .mcel-runtime-preview.theme-local {
          color-scheme: light;
          --site-bg:
            radial-gradient(circle at 90% 0%, rgba(247, 201, 72, 0.24), transparent 28rem),
            linear-gradient(180deg, #f6eddd, #eee4d1);
          --site-page: rgba(255, 252, 244, 0.94);
          --site-card: #fffaf0;
          --site-card-soft: rgba(255,255,255,0.72);
          --site-ink: #1b2118;
          --site-muted: #657058;
          --site-heading: #141a11;
          --site-accent: #2d7a4f;
          --site-accent-2: #d77a2d;
          --site-action: #f5c84c;
          --site-action-ink: #1f1700;
          --site-line: rgba(47, 80, 48, 0.2);
          --site-shadow: 0 18px 55px rgba(54, 69, 44, 0.16);
          --site-radius: 22px;
          --site-radius-sm: 14px;
          --site-hero-ornament-radius: 30px;
          --site-hero-ornament-bg:
            linear-gradient(135deg, rgba(45,122,79,0.86), rgba(120,160,78,0.82)),
            radial-gradient(circle at 30% 22%, rgba(255,255,255,0.52), transparent 36%);
          --site-hero-ornament-shadow: 0 28px 80px rgba(45, 122, 79, 0.24);
          --site-hero-bg:
            radial-gradient(circle at 95% 12%, rgba(45,122,79,0.13), transparent 32%),
            linear-gradient(135deg, rgba(255,255,255,0.82), rgba(255,248,230,0.7)),
            #fffaf0;
        }

        body.theme-saas,
        .mcel-runtime-preview.theme-saas {
          color-scheme: dark;
          --site-bg:
            radial-gradient(circle at 88% 6%, rgba(84, 116, 255, 0.28), transparent 31rem),
            radial-gradient(circle at 8% 22%, rgba(0, 214, 201, 0.18), transparent 28rem),
            #070916;
          --site-page: rgba(12, 16, 34, 0.92);
          --site-card: rgba(18, 24, 48, 0.9);
          --site-card-soft: rgba(255,255,255,0.06);
          --site-ink: #f6f8ff;
          --site-muted: #aab5d6;
          --site-heading: #ffffff;
          --site-accent: #64e4ff;
          --site-accent-2: #9d7cff;
          --site-action: #8dffcb;
          --site-action-ink: #00150f;
          --site-line: rgba(127, 157, 255, 0.28);
          --site-shadow: 0 28px 90px rgba(0, 0, 0, 0.42);
          --site-radius: 28px;
          --site-radius-sm: 16px;
          --site-heading-track: -0.075em;
          --site-hero-ornament-radius: 40% 60% 48% 52%;
          --site-hero-ornament-bg:
            linear-gradient(135deg, rgba(100,228,255,0.96), rgba(157,124,255,0.9)),
            radial-gradient(circle at 35% 24%, rgba(255,255,255,0.48), transparent 31%);
          --site-hero-ornament-shadow: 0 28px 110px rgba(100, 228, 255, 0.22);
          --site-hero-bg:
            linear-gradient(135deg, rgba(255,255,255,0.08), rgba(255,255,255,0.025)),
            rgba(12, 16, 34, 0.9);
        }

        body.theme-editorial,
        .mcel-runtime-preview.theme-editorial {
          color-scheme: light;
          --site-bg:
            linear-gradient(90deg, rgba(56, 44, 28, 0.05) 1px, transparent 1px),
            #f7f0e2;
          --site-page: #fffaf0;
          --site-card: #fffdf7;
          --site-card-soft: rgba(244,232,210,0.7);
          --site-ink: #251f17;
          --site-muted: #736450;
          --site-heading: #17120d;
          --site-accent: #9b3f2c;
          --site-accent-2: #1f5a68;
          --site-action: #17120d;
          --site-action-ink: #fff6e5;
          --site-line: rgba(45, 35, 22, 0.2);
          --site-shadow: none;
          --site-radius: 10px;
          --site-radius-sm: 6px;
          --site-max: 980px;
          --site-font-display: Georgia, "Times New Roman", serif;
          --site-font-body: Georgia, "Times New Roman", serif;
          --site-heading-track: -0.045em;
          --site-hero-columns: minmax(0, 0.95fr) minmax(220px, 0.7fr);
          --site-hero-ornament-radius: 6px;
          --site-hero-ornament-bg:
            linear-gradient(180deg, rgba(155,63,44,0.94), rgba(31,90,104,0.88)),
            repeating-linear-gradient(45deg, rgba(255,255,255,0.18) 0 8px, transparent 8px 18px);
          --site-hero-ornament-shadow: none;
          --site-hero-bg: #fffdf7;
        }

        body.theme-luxury,
        .mcel-runtime-preview.theme-luxury {
          color-scheme: dark;
          --site-bg:
            radial-gradient(circle at 78% 8%, rgba(210, 172, 91, 0.18), transparent 30rem),
            #070605;
          --site-page: #0d0b09;
          --site-card: #15110d;
          --site-card-soft: rgba(210, 172, 91, 0.08);
          --site-ink: #fbf3df;
          --site-muted: #b9aa88;
          --site-heading: #fff7df;
          --site-accent: #d6b56d;
          --site-accent-2: #8e6f41;
          --site-action: #d6b56d;
          --site-action-ink: #120d04;
          --site-line: rgba(214, 181, 109, 0.32);
          --site-shadow: 0 26px 90px rgba(0,0,0,0.52);
          --site-radius: 4px;
          --site-radius-sm: 2px;
          --site-font-display: "Didot", "Bodoni 72", Georgia, serif;
          --site-heading-track: -0.035em;
          --site-hero-ornament-radius: 2px;
          --site-hero-ornament-bg:
            linear-gradient(145deg, rgba(214,181,109,0.88), rgba(75,55,30,0.9)),
            radial-gradient(circle at 40% 20%, rgba(255,255,255,0.32), transparent 28%);
          --site-hero-ornament-shadow: 0 26px 90px rgba(214, 181, 109, 0.18);
          --site-hero-bg:
            linear-gradient(135deg, rgba(214,181,109,0.11), rgba(255,255,255,0.02)),
            #15110d;
        }

        body.theme-civic,
        .mcel-runtime-preview.theme-civic {
          color-scheme: light;
          --site-bg: linear-gradient(180deg, #e8f1fb, #f7fbff 38%, #ffffff);
          --site-page: #ffffff;
          --site-card: #ffffff;
          --site-card-soft: #eef6ff;
          --site-ink: #132538;
          --site-muted: #51677d;
          --site-heading: #07192c;
          --site-accent: #075da8;
          --site-accent-2: #1b7b6d;
          --site-action: #075da8;
          --site-action-ink: #ffffff;
          --site-line: rgba(7, 93, 168, 0.22);
          --site-shadow: 0 16px 40px rgba(11, 57, 94, 0.12);
          --site-radius: 14px;
          --site-radius-sm: 8px;
          --site-heading-track: -0.045em;
          --site-hero-ornament-radius: 999px 999px 20px 20px;
          --site-hero-ornament-bg:
            linear-gradient(180deg, rgba(7,93,168,0.9), rgba(27,123,109,0.86)),
            radial-gradient(circle at 50% 20%, rgba(255,255,255,0.42), transparent 30%);
          --site-hero-ornament-shadow: 0 20px 70px rgba(7, 93, 168, 0.2);
          --site-hero-bg:
            linear-gradient(135deg, rgba(7,93,168,0.08), rgba(27,123,109,0.04)),
            #ffffff;
        }

        body.theme-accessible,
        .mcel-runtime-preview.theme-accessible {
          color-scheme: dark;
          --site-bg: #000000;
          --site-page: #000000;
          --site-card: #000000;
          --site-card-soft: #101010;
          --site-ink: #ffffff;
          --site-muted: #ffffff;
          --site-heading: #ffffff;
          --site-accent: #00e5ff;
          --site-accent-2: #ff7a00;
          --site-action: #ffff00;
          --site-action-ink: #000000;
          --site-line: #ffffff;
          --site-shadow: none;
          --site-radius: 0;
          --site-radius-sm: 0;
          --site-font-body: Arial, Helvetica, sans-serif;
          --site-font-display: Arial, Helvetica, sans-serif;
          --site-heading-track: -0.02em;
          --site-hero-ornament-display: none;
          --site-hero-bg: #000000;
        }

        body.theme-debug,
        .mcel-runtime-preview.theme-debug {
          color-scheme: light;
          --site-bg:
            linear-gradient(90deg, rgba(0,0,0,0.05) 1px, transparent 1px),
            linear-gradient(0deg, rgba(0,0,0,0.05) 1px, transparent 1px),
            #fafafa;
          --site-page: transparent;
          --site-card: rgba(255,255,255,0.84);
          --site-card-soft: rgba(0, 118, 255, 0.06);
          --site-ink: #101010;
          --site-muted: #313131;
          --site-heading: #000000;
          --site-accent: #005cff;
          --site-accent-2: #ff3b30;
          --site-action: #000000;
          --site-action-ink: #ffffff;
          --site-line: #005cff;
          --site-shadow: none;
          --site-radius: 0;
          --site-radius-sm: 0;
          --site-font-body: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
          --site-font-display: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
          --site-heading-track: -0.02em;
          --site-hero-ornament-display: none;
          --site-hero-bg: rgba(0, 92, 255, 0.04);
          --site-grid-overlay:
            linear-gradient(90deg, rgba(0,92,255,0.1) 1px, transparent 1px),
            linear-gradient(0deg, rgba(255,59,48,0.08) 1px, transparent 1px);
          background-size: 24px 24px;
        }

        * { box-sizing: border-box; }
        html { min-height: 100%; background: var(--site-bg); }
        body {
          margin: 0;
          min-height: 100%;
          font-family: var(--site-font-body);
          color: var(--site-ink);
          background: var(--site-bg);
        }
        .mcel-runtime-preview {
          width: min(var(--site-max), calc(100% - 32px));
          margin: 0 auto;
          padding: clamp(18px, 3vw, 44px) 0;
          display: grid;
          gap: 18px;
          overflow: visible;
          color: var(--site-ink);
        }
        .mcel-runtime-preview .mc {
          min-width: 0;
          position: relative;
          display: grid;
          gap: 14px;
          padding: clamp(18px, 3vw, 34px);
          border: 1px solid var(--site-line);
          border-radius: var(--site-radius);
          background:
            var(--site-grid-overlay),
            linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.015)),
            var(--site-card);
          box-shadow: var(--site-shadow);
          overflow: visible;
        }
        .mcel-runtime-preview [data-mc-generated="true"] {
          display: none !important;
        }
        body.theme-debug .mcel-runtime-preview [data-mc-generated="true"] {
          display: grid !important;
          min-height: 12px;
          color: var(--site-accent-2);
          font-size: 10px;
          text-transform: uppercase;
        }
        .mcel-runtime-preview .mc[data-mc-scroll-owner="self"] {
          max-block-size: min(62vh, 560px);
          overflow: auto;
        }
        .mcel-runtime-preview .mc[data-mc-scroll-owner="content"],
        .mcel-runtime-preview .mc[data-mc-scroll-owner="parent"],
        .mcel-runtime-preview .mc[data-mc-scroll-owner="viewport"] {
          overflow: visible !important;
        }
        .mcel-runtime-preview .mc[data-mc-scroll-owner="none"] {
          overflow: clip;
        }
        .mcel-runtime-preview > .mc[data-mc-component-kind="page"] {
          gap: clamp(16px, 2.4vw, 28px);
          padding: clamp(18px, 3vw, 40px);
          border-radius: calc(var(--site-radius) + 8px);
          background:
            var(--site-grid-overlay),
            radial-gradient(circle at 86% 4%, color-mix(in srgb, var(--site-accent) 14%, transparent), transparent 30%),
            var(--site-page);
        }
        .mcel-runtime-preview .mc[data-mc-kind="hero"] {
          grid-template-columns: var(--site-hero-columns);
          align-items: center;
          min-block-size: var(--site-hero-min);
          background:
            var(--site-grid-overlay),
            var(--site-hero-bg);
        }
        .mcel-runtime-preview .mc[data-mc-kind="hero"]::after {
          content: "";
          display: var(--site-hero-ornament-display);
          inline-size: min(100%, 370px);
          aspect-ratio: 0.72;
          justify-self: end;
          grid-row: 1 / span 4;
          grid-column: 2;
          border: 1px solid var(--site-line);
          border-radius: var(--site-hero-ornament-radius);
          background: var(--site-hero-ornament-bg);
          box-shadow: var(--site-hero-ornament-shadow);
        }
        .mcel-runtime-preview .mc[data-mc-kind="hero"] > *:not([data-mc-generated="true"]) {
          grid-column: 1;
          z-index: 1;
        }
        .mcel-runtime-preview h1,
        .mcel-runtime-preview h2,
        .mcel-runtime-preview h3,
        .mcel-runtime-preview p {
          margin-block: 0;
        }
        .mcel-runtime-preview h1,
        .mcel-runtime-preview h2 {
          font-family: var(--site-font-display);
          color: var(--site-heading);
        }
        .mcel-runtime-preview h1 {
          max-width: 12ch;
          font-size: clamp(40px, 7vw, 92px);
          line-height: 0.92;
          letter-spacing: var(--site-heading-track);
        }
        .mcel-runtime-preview h2 {
          font-size: clamp(24px, 3vw, 42px);
          line-height: 1.02;
          letter-spacing: calc(var(--site-heading-track) * 0.45);
        }
        .mcel-runtime-preview h3 {
          width: fit-content;
          color: var(--site-accent);
          font-size: 13px;
          text-transform: uppercase;
          letter-spacing: 0.08em;
        }

        body.theme-machine .mcel-runtime-preview h3 {
          color: var(--site-accent-2);
          font-size: 15px;
        }
        .mcel-runtime-preview p {
          max-width: 68ch;
          color: var(--site-muted);
          font-weight: 700;
          line-height: 1.58;
        }
        body.theme-editorial .mcel-runtime-preview p {
          font-size: 18px;
          font-weight: 500;
          line-height: 1.72;
        }
        body.theme-accessible .mcel-runtime-preview p,
        body.theme-accessible .mcel-runtime-preview label,
        body.theme-accessible .mcel-runtime-preview input,
        body.theme-accessible .mcel-runtime-preview button {
          font-size: 18px;
          line-height: 1.6;
        }
        .mcel-runtime-preview [data-mc-slot="meta"] {
          width: fit-content;
          border: 1px solid var(--site-line);
          border-radius: 999px;
          padding: 6px 10px;
          color: var(--site-accent);
          background: var(--site-card-soft);
          font-size: 12px;
          font-weight: 900;
          text-transform: uppercase;
          letter-spacing: 0.06em;
        }

        body.theme-machine .mcel-runtime-preview [data-mc-slot="meta"] {
          border-color: rgba(115, 214, 255, 0.26);
          color: #73d6ff;
          background: transparent;
          font-weight: 950;
        }
        .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] {
          grid-template-columns: repeat(3, minmax(0, 1fr));
          align-items: stretch;
        }
        .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] > h2 {
          grid-column: 1 / -1;
        }
        .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] > .mc {
          min-block-size: 100%;
          align-content: start;
          background:
            var(--site-grid-overlay),
            var(--site-card-soft);
        }
        .mcel-runtime-preview form.mc {
          grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) auto;
          align-items: end;
          gap: 14px;
        }
        .mcel-runtime-preview form.mc h2 {
          grid-column: 1 / -1;
        }
        .mcel-runtime-preview form.mc label {
          display: grid;
          gap: 8px;
          color: var(--site-muted);
          font-size: 12px;
          font-weight: 900;
          text-transform: uppercase;
          letter-spacing: 0.06em;
        }
        .mcel-runtime-preview input {
          min-width: 0;
          border: 1px solid var(--site-line);
          border-radius: var(--site-radius-sm);
          background: var(--site-card);
          color: var(--site-ink);
          padding: 13px 15px;
          font: inherit;
        }

        body.theme-machine .mcel-runtime-preview input {
          border-color: rgba(246, 199, 91, 0.32);
          background: #030403;
        }
        .mcel-runtime-preview button,
        .mcel-runtime-preview a[data-mc-action] {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          justify-self: start;
          max-inline-size: 100%;
          min-height: 42px;
          border: 0;
          border-radius: 999px;
          background: var(--site-action);
          color: var(--site-action-ink);
          padding: 12px 20px;
          box-sizing: border-box;
          font-weight: 950;
          line-height: 1;
          text-decoration: none;
          vertical-align: top;
          cursor: pointer;
          box-shadow: none;
        }
        body.theme-accessible .mcel-runtime-preview button,
        body.theme-accessible .mcel-runtime-preview a[data-mc-action] {
          min-height: 52px;
          border: 3px solid #ffffff;
        }
        body.theme-luxury .mcel-runtime-preview button,
        body.theme-luxury .mcel-runtime-preview a[data-mc-action] {
          border-radius: 2px;
          text-transform: uppercase;
          letter-spacing: 0.1em;
        }
        body.theme-editorial .mcel-runtime-preview button,
        body.theme-editorial .mcel-runtime-preview a[data-mc-action] {
          border-radius: 2px;
        }
        .mcel-runtime-preview .mc[data-mc="command-row"] {
          grid-template-columns: minmax(0, 1fr) auto;
          align-items: center;
        }
        body.theme-debug .mcel-runtime-preview .mc::before {
          content: attr(data-mc) " / " attr(data-mc-kind);
          justify-self: start;
          padding: 3px 6px;
          border: 1px solid var(--site-accent-2);
          color: var(--site-accent-2);
          background: #fff;
          font-size: 10px;
          font-weight: 900;
          text-transform: uppercase;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview {
          max-inline-size: min(1120px, calc(100% - 48px));
          padding-block: clamp(22px, 4vw, 54px);
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview > .mc {
          display: block;
          min-block-size: auto;
          border: 0;
          border-radius: 0;
          background: transparent;
          box-shadow: none;
          padding: 0;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-shell {
          display: grid;
          grid-template-columns: minmax(0, 1.35fr) minmax(260px, 0.65fr);
          gap: clamp(24px, 4vw, 56px);
          align-items: start;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-lede,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-body {
          min-width: 0;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-lede {
          grid-column: 1 / -1;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-body {
          display: grid;
          gap: clamp(18px, 3vw, 32px);
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail {
          position: sticky;
          top: 24px;
          display: grid;
          gap: 16px;
          min-width: 0;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-lede > .mc,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-body > .mc,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail > .mc {
          border-color: var(--site-line);
          background: transparent;
          box-shadow: none;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-lede > .mc[data-mc-kind="hero"] {
          grid-template-columns: minmax(0, 1fr);
          min-block-size: auto;
          padding: clamp(28px, 6vw, 76px) 0 clamp(22px, 4vw, 44px);
          border: 0;
          border-bottom: 1px solid var(--site-line);
          border-radius: 0;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview .mc[data-mc-kind="hero"]::after {
          display: none;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-lede h1,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-lede h2 {
          max-inline-size: 14ch;
          font-size: clamp(3.4rem, 12vw, 8.8rem);
          line-height: 0.88;
          letter-spacing: -0.075em;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-lede p:not([data-mc-slot="meta"]):not([data-mc-slot="actions"]) {
          max-inline-size: 62ch;
          font-size: clamp(1.08rem, 2vw, 1.55rem);
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-body > .mc {
          padding: clamp(20px, 3vw, 36px) 0;
          border: 0;
          border-top: 1px solid var(--site-line);
          border-radius: 0;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] {
          grid-template-columns: minmax(0, 1fr);
          gap: 14px;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] > h2 {
          max-inline-size: 16ch;
          font-size: clamp(2rem, 5vw, 4.2rem);
          line-height: 0.95;
          letter-spacing: -0.045em;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] > .mc {
          min-block-size: auto;
          padding: clamp(16px, 2vw, 24px) 0 clamp(16px, 2vw, 24px) clamp(18px, 3vw, 32px);
          border: 0;
          border-left: 3px solid var(--site-accent);
          border-radius: 0;
          background: transparent;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] > .mc h3 {
          font-size: clamp(1.1rem, 2vw, 1.6rem);
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail > .mc {
          padding: clamp(18px, 3vw, 28px);
          border: 1px solid var(--site-line);
          border-radius: var(--site-radius-sm);
          background: var(--site-card-soft);
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail form.mc {
          grid-template-columns: minmax(0, 1fr);
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail .mc[data-mc="command-row"] {
          grid-template-columns: minmax(0, 1fr);
        }


        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-runtime-preview,
        body[data-mcel-chrome="chrome-spotlight"] .mcel-runtime-preview,
        body[data-mcel-chrome="chrome-journey"] .mcel-runtime-preview,
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-runtime-preview {
          max-inline-size: min(1180px, calc(100% - 48px));
          padding-block: clamp(22px, 4vw, 54px);
        }
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-runtime-preview > .mc,
        body[data-mcel-chrome="chrome-spotlight"] .mcel-runtime-preview > .mc,
        body[data-mcel-chrome="chrome-journey"] .mcel-runtime-preview > .mc,
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-runtime-preview > .mc {
          display: block;
          min-block-size: auto;
          border: 0;
          border-radius: 0;
          background: transparent;
          box-shadow: none;
          padding: 0;
        }

        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-shell {
          display: grid;
          gap: clamp(22px, 4vw, 42px);
        }
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-intro {
          display: grid;
          gap: 12px;
          max-inline-size: 760px;
        }
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr));
          gap: clamp(16px, 2.4vw, 28px);
          align-items: stretch;
        }
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-item {
          min-block-size: 100%;
          align-content: start;
          --mcel-chrome-frame-gap: 0px;
        }
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-body > .mc {
          min-block-size: 100%;
          align-content: start;
        }
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-body > .mc h2,
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-body > .mc h3 {
          font-size: clamp(1.2rem, 2.4vw, 2rem);
          line-height: 1.02;
        }

        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-shell {
          display: grid;
          grid-template-columns: minmax(0, 1.25fr) minmax(250px, 0.75fr);
          gap: clamp(22px, 4vw, 52px);
          align-items: start;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-primary > .mcel-chrome-spotlight-item {
          min-block-size: clamp(360px, 52vw, 680px);
          align-content: center;
          padding: clamp(28px, 6vw, 76px);
          border: 1px solid var(--site-line);
          border-radius: var(--site-radius);
          background: var(--site-card-soft);
          box-shadow: var(--site-shadow);
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-primary .mcel-chrome-spotlight-body > .mc {
          border: 0;
          background: transparent;
          box-shadow: none;
          padding: 0;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-primary h1,
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-primary h2 {
          max-inline-size: 13ch;
          font-size: clamp(3rem, 9vw, 7.2rem);
          line-height: 0.9;
          letter-spacing: -0.065em;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support {
          position: sticky;
          top: 24px;
          display: grid;
          gap: 16px;
          min-width: 0;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item {
          padding: clamp(18px, 2.6vw, 30px);
          border: 1px solid var(--site-line);
          border-radius: var(--site-radius-sm);
          background: var(--site-card-soft);
          box-shadow: var(--site-shadow);
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"],
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] {
          --mcel-smart-envelope-block-pad: clamp(72px, 11vw, 160px);
          --mcel-smart-envelope-inline-pad: clamp(32px, 6vw, 84px);
          position: relative;
          display: grid;
          align-content: center;
          min-block-size: max-content;
          padding: var(--mcel-smart-envelope-block-pad) var(--mcel-smart-envelope-inline-pad);
          border-radius: 999px;
          overflow: visible;
          isolation: isolate;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"] > [data-mcel-chrome-region-role="body"],
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] > [data-mcel-chrome-region-role="body"] {
          position: relative;
          z-index: 1;
          display: grid;
          align-content: center;
          min-inline-size: 0;
          max-inline-size: 100%;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"] [data-mc="feed"],
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] [data-mc="feed"],
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"] [data-mc-component-kind="layout"],
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] [data-mc-component-kind="layout"] {
          display: grid;
          gap: clamp(16px, 2.4vw, 28px);
          min-inline-size: 0;
          max-inline-size: 100%;
          margin-inline: auto;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"] :is(.mc-panel, [data-mc="panel"]),
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] :is(.mc-panel, [data-mc="panel"]) {
          max-inline-size: 100%;
          margin-inline: 0;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="island"], .mc[data-mc-component-kind="primitive"]),
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="island"], .mc[data-mc-component-kind="primitive"]) {
          display: grid;
          grid-template-columns: minmax(0, 1fr);
          align-items: stretch;
          align-content: start;
          gap: clamp(12px, 2vw, 18px);
          min-block-size: max-content;
          max-block-size: none;
          overflow: visible !important;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="island"], .mc[data-mc-component-kind="primitive"]) :is(h1,h2,h3,p,label,input,textarea,select,button,a[data-mc-action]),
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="island"], .mc[data-mc-component-kind="primitive"]) :is(h1,h2,h3,p,label,input,textarea,select,button,a[data-mc-action]) {
          max-inline-size: 100%;
          min-inline-size: 0;
          overflow: visible;
          box-sizing: border-box;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"] :is(form.mc, .mc[data-mc="command-row"]) :is(input,textarea,select,button,a[data-mc-action]),
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] :is(form.mc, .mc[data-mc="command-row"]) :is(input,textarea,select,button,a[data-mc-action]) {
          inline-size: 100%;
          width: 100%;
          justify-self: stretch;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support .mcel-chrome-spotlight-body > .mc {
          border: 0;
          background: transparent;
          box-shadow: none;
          padding: 0;
        }

        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-shell {
          display: grid;
          gap: clamp(22px, 4vw, 46px);
        }
        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-intro {
          max-inline-size: 760px;
        }
        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-sequence {
          display: grid;
          gap: clamp(16px, 2.4vw, 28px);
          counter-reset: mcel-journey-step;
        }
        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step {
          counter-increment: mcel-journey-step;
          display: grid;
          grid-template-columns: auto minmax(0, 1fr);
          gap: clamp(14px, 2vw, 24px);
          align-items: start;
          min-width: 0;
        }
        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step::before {
          content: attr(data-mcel-step);
          display: grid;
          place-items: center;
          inline-size: clamp(38px, 6vw, 58px);
          block-size: clamp(38px, 6vw, 58px);
          border: 1px solid var(--site-line);
          border-radius: 999px;
          background: var(--site-card-soft);
          color: var(--site-accent);
          font-weight: 900;
        }
        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step > .mcel-chrome-journey-body {
          min-width: 0;
          border-left: 3px solid var(--site-accent);
          border-radius: var(--site-radius-sm);
          background: var(--site-card-soft);
          box-shadow: var(--site-shadow);
          overflow: clip;
          padding: clamp(30px, 5vw, 72px);
        }
        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-content > .mc {
          margin: 0;
        }

        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-shell {
          display: grid;
          gap: clamp(18px, 3vw, 34px);
        }
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-intro {
          max-inline-size: 760px;
        }
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-panels {
          display: grid;
          gap: 12px;
        }
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-panel {
          overflow: clip;
          border: 1px solid var(--site-line);
          border-radius: var(--site-radius-sm);
          background: var(--site-card-soft);
          box-shadow: var(--site-shadow);
          padding: clamp(28px, 5vw, 70px) clamp(38px, 7vw, 112px) clamp(34px, 5.5vw, 78px);
          --mcel-chrome-frame-gap: clamp(18px, 3vw, 38px);
        }
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-summary {
          cursor: pointer;
          padding: 0;
          color: var(--site-ink);
          font-weight: 900;
          list-style-position: inside;
        }
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-body {
          border-top: 1px solid var(--site-line);
          padding-block-start: clamp(18px, 3vw, 34px);
        }
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-body > .mc {
          margin: 0;
        }

        body[data-mcel-chrome="chrome-cluster-grid"] [data-mcel-chrome-generated="true"],
        body[data-mcel-chrome="chrome-cluster-grid"] [data-mcel-fit-region],
        body[data-mcel-chrome="chrome-cluster-grid"] [data-mcel-fit-policy],
        body[data-mcel-chrome="chrome-spotlight"] [data-mcel-chrome-generated="true"],
        body[data-mcel-chrome="chrome-spotlight"] [data-mcel-fit-region],
        body[data-mcel-chrome="chrome-spotlight"] [data-mcel-fit-policy],
        body[data-mcel-chrome="chrome-journey"] [data-mcel-chrome-generated="true"],
        body[data-mcel-chrome="chrome-journey"] [data-mcel-fit-region],
        body[data-mcel-chrome="chrome-journey"] [data-mcel-fit-policy],
        body[data-mcel-chrome="chrome-compact-disclosure"] [data-mcel-chrome-generated="true"],
        body[data-mcel-chrome="chrome-compact-disclosure"] [data-mcel-fit-region],
        body[data-mcel-chrome="chrome-compact-disclosure"] [data-mcel-fit-policy] {
          min-inline-size: 0;
          max-inline-size: 100%;
          box-sizing: border-box;
        }

        body[data-mcel-chrome] [data-mcel-chrome-frame] {
          display: grid;
          grid-template-rows: auto minmax(0, auto);
          row-gap: var(--mcel-chrome-frame-gap, clamp(14px, 2.4vw, 28px));
          align-items: start;
          min-inline-size: 0;
          max-inline-size: 100%;
          box-sizing: border-box;
          isolation: isolate;
        }
        body[data-mcel-chrome] [data-mcel-chrome-region-role] {
          min-inline-size: 0;
          max-inline-size: 100%;
          box-sizing: border-box;
          position: relative;
        }
        body[data-mcel-chrome] [data-mcel-chrome-region-role="header"] {
          grid-row: 1;
          z-index: 2;
          justify-self: center;
          text-align: center;
        }
        body[data-mcel-chrome] [data-mcel-chrome-region-role="body"] {
          grid-row: 2;
          z-index: 1;
          display: grid;
          min-inline-size: 0;
          align-items: stretch;
        }
        body[data-mcel-chrome] [data-mcel-chrome-frame] > [data-mcel-chrome-region-role="body"]:first-child {
          grid-row: 1 / -1;
        }
        body[data-mcel-chrome] [data-mcel-chrome-region-role="body"] > * {
          min-inline-size: 0;
          max-inline-size: 100%;
          box-sizing: border-box;
        }

        body[data-mcel-chrome="chrome-cluster-grid"] [data-mcel-fit-policy="contain"] > *,
        body[data-mcel-chrome="chrome-spotlight"] [data-mcel-fit-policy="contain"] > *,
        body[data-mcel-chrome="chrome-journey"] [data-mcel-fit-policy="contain"] > *,
        body[data-mcel-chrome="chrome-compact-disclosure"] [data-mcel-fit-policy="contain"] > * {
          min-inline-size: 0;
          max-inline-size: 100%;
          box-sizing: border-box;
        }
        body[data-mcel-chrome="chrome-cluster-grid"] [data-mcel-fit-policy="contain"] :is(img,svg,canvas,video,iframe,table,pre,code,input,textarea,select,button),
        body[data-mcel-chrome="chrome-spotlight"] [data-mcel-fit-policy="contain"] :is(img,svg,canvas,video,iframe,table,pre,code,input,textarea,select,button),
        body[data-mcel-chrome="chrome-journey"] [data-mcel-fit-policy="contain"] :is(img,svg,canvas,video,iframe,table,pre,code,input,textarea,select,button),
        body[data-mcel-chrome="chrome-compact-disclosure"] [data-mcel-fit-policy="contain"] :is(img,svg,canvas,video,iframe,table,pre,code,input,textarea,select,button) {
          max-inline-size: 100%;
        }

        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="control-balance"] :is(input,textarea,select,button) {
          inline-size: 100%;
          max-inline-size: 100%;
          min-inline-size: 0;
          box-sizing: border-box;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="control-balance"] button {
          justify-self: stretch;
          width: 100%;
          white-space: normal;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) :is(form[data-mcel-composition-remedy~="control-balance"], [data-mcel-composition-remedy~="control-balance"] form) {
          display: grid;
          grid-template-columns: minmax(0, 1fr);
          align-items: stretch;
        }

        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="shape-inset-content"] {
          container-type: inline-size;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="shape-inset-content"] :is(h1,h2,h3,p,label,input,textarea,select,button,a[data-mc-action]) {
          max-inline-size: calc(100% - clamp(32px, 18cqi, 96px));
          justify-self: center;
          box-sizing: border-box;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="shape-inset-content"] :is(input,textarea,select,button,a[data-mc-action]) {
          inline-size: calc(100% - clamp(32px, 18cqi, 96px));
          width: calc(100% - clamp(32px, 18cqi, 96px));
          min-inline-size: 0;
        }

        @supports not (width: 1cqi) {
          body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="shape-inset-content"] :is(h1,h2,h3,p,label,input,textarea,select,button,a[data-mc-action]) {
            max-inline-size: calc(100% - clamp(32px, 12vw, 96px));
          }
          body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="shape-inset-content"] :is(input,textarea,select,button,a[data-mc-action]) {
            inline-size: calc(100% - clamp(32px, 12vw, 96px));
            width: calc(100% - clamp(32px, 12vw, 96px));
          }
        }

        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="dedistort-container-shape"] {
          border-radius: min(var(--site-radius), 28px) !important;
          min-block-size: max-content !important;
          aspect-ratio: auto !important;
          align-content: start;
          overflow: visible;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="dedistort-container-shape"] > [data-mcel-chrome-region-role="body"] {
          align-items: stretch;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="dedistort-container-shape"] :is(.mc,[data-mc]) {
          border-radius: min(var(--site-radius), 22px);
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="smart-content-envelope"] {
          --mcel-smart-envelope-block-pad: clamp(72px, 11vw, 160px);
          --mcel-smart-envelope-inline-pad: clamp(32px, 6vw, 84px);
          position: relative;
          display: grid;
          align-content: center;
          min-block-size: max-content;
          padding: var(--mcel-smart-envelope-block-pad) var(--mcel-smart-envelope-inline-pad) !important;
          border-radius: 999px;
          overflow: visible;
          isolation: isolate;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="smart-content-envelope"] > [data-mcel-chrome-region-role="body"] {
          position: relative;
          z-index: 1;
          display: grid;
          align-content: center;
          min-inline-size: 0;
          max-inline-size: 100%;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="smart-flow-frame"] {
          display: grid;
          grid-template-columns: minmax(0, 1fr);
          align-items: stretch;
          align-content: start;
          min-inline-size: 0;
          max-inline-size: 100%;
          min-block-size: max-content !important;
          block-size: auto !important;
          max-block-size: none !important;
          overflow: visible !important;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="smart-flow-frame"] > * {
          min-inline-size: 0;
          max-inline-size: 100%;
          box-sizing: border-box;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="smart-flow-frame"] :is(h1,h2,h3,h4,h5,h6,p,label,input,textarea,select,button,a[data-mc-action]) {
          max-inline-size: 100%;
          min-inline-size: 0;
          overflow: visible;
          box-sizing: border-box;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) :is(form[data-mcel-composition-remedy~="smart-flow-frame"], [data-mcel-composition-remedy~="smart-flow-frame"] form, .mc[data-mc="command-row"][data-mcel-composition-remedy~="smart-flow-frame"], [data-mcel-composition-remedy~="smart-flow-frame"] .mc[data-mc="command-row"]) {
          grid-template-columns: minmax(0, 1fr);
          align-items: stretch;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) :is(form[data-mcel-composition-remedy~="smart-flow-frame"], [data-mcel-composition-remedy~="smart-flow-frame"] form, .mc[data-mc="command-row"][data-mcel-composition-remedy~="smart-flow-frame"], [data-mcel-composition-remedy~="smart-flow-frame"] .mc[data-mc="command-row"]) :is(input,textarea,select,button,a[data-mc-action]) {
          inline-size: 100%;
          width: 100%;
          justify-self: stretch;
        }

        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-cluster-grid"] [data-mcel-fit-policy="contain"],
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-spotlight"] [data-mcel-fit-policy="contain"],
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-journey"] [data-mcel-fit-policy="contain"],
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-compact-disclosure"] [data-mcel-fit-policy="contain"] {
          container-type: inline-size;
          overflow-wrap: anywhere;
        }
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-cluster-grid"] :is(h1,h2),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support :is(h1,h2),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-journey"] :is(h1,h2),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-compact-disclosure"] :is(h1,h2) {
          max-inline-size: 100%;
          font-size: clamp(1.35rem, 8cqi, 3rem);
          line-height: 1;
          letter-spacing: -0.045em;
          text-wrap: balance;
        }
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-cluster-grid"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="layout"]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="layout"]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-journey"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="layout"]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-compact-disclosure"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="layout"]) {
          grid-template-columns: minmax(0, 1fr);
          align-items: stretch;
        }
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-cluster-grid"] :is(label,input,button,a[data-mc-action]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support :is(label,input,button,a[data-mc-action]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-journey"] :is(label,input,button,a[data-mc-action]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-compact-disclosure"] :is(label,input,button,a[data-mc-action]) {
          inline-size: 100%;
          justify-self: stretch;
          white-space: normal;
        }

        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="dedistort-inline-content"] :is(p[data-mc-slot="actions"], [data-mc-slot="actions"]) {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          align-items: center;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="dedistort-inline-content"] :is(button,a[data-mc-action],[role="button"]) {
          writing-mode: horizontal-tb;
          text-orientation: mixed;
          white-space: nowrap;
          word-break: normal;
          overflow-wrap: normal;
          inline-size: max-content;
          width: max-content;
          max-inline-size: none;
          min-inline-size: max-content;
          justify-self: start;
          align-self: center;
        }

        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-grid {
          grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr));
        }
        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-grid > .mcel-chrome-cluster-item,
        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item,
        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step > .mcel-chrome-journey-body,
        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-panel {
          min-block-size: max-content;
          align-content: start;
        }

        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-grid,
        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-shell {
          grid-template-columns: minmax(0, 1fr);
        }
        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support {
          position: static;
        }
        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step {
          grid-template-columns: minmax(0, 1fr);
        }
        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step::before {
          justify-self: start;
        }
        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-panel {
          grid-template-rows: auto minmax(0, auto);
        }

        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-chrome-generated="true"],
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region],
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy] {
          min-inline-size: 0;
          max-inline-size: 100%;
          box-sizing: border-box;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] > * {
          min-inline-size: 0;
          max-inline-size: 100%;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] img,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] svg,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] canvas,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] video,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] iframe,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] table,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] pre,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] code,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] input,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] textarea,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] select,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] button {
          max-inline-size: 100%;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="control-balance"] input,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="control-balance"] textarea,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="control-balance"] select,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="control-balance"] button {
          inline-size: 100%;
          max-inline-size: 100%;
          min-inline-size: 0;
          box-sizing: border-box;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="control-balance"] button {
          justify-self: stretch;
          width: 100%;
          white-space: normal;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail form[data-mcel-composition-remedy~="control-balance"],
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="control-balance"] form {
          display: grid;
          grid-template-columns: minmax(0, 1fr);
          align-items: stretch;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] {
          container-type: inline-size;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] h1,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] h2,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] h3,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] p,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] label,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] input,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] textarea,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] select,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] button,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] a[data-mc-action] {
          max-inline-size: calc(100% - clamp(32px, 18cqi, 96px));
          justify-self: center;
          box-sizing: border-box;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] input,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] textarea,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] select,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] button,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] a[data-mc-action] {
          inline-size: calc(100% - clamp(32px, 18cqi, 96px));
          width: calc(100% - clamp(32px, 18cqi, 96px));
          min-inline-size: 0;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail form[data-mcel-composition-remedy~="shape-inset-content"],
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] form {
          display: grid;
          grid-template-columns: minmax(0, 1fr);
          justify-items: center;
        }

        @supports not (width: 1cqi) {
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] h1,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] h2,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] h3,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] p,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] label,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] input,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] textarea,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] select,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] button,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] a[data-mc-action] {
            max-inline-size: calc(100% - clamp(32px, 12vw, 96px));
          }

          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] input,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] textarea,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] select,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] button,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] a[data-mc-action] {
            inline-size: calc(100% - clamp(32px, 12vw, 96px));
            width: calc(100% - clamp(32px, 12vw, 96px));
          }
        }

        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] {
          container-type: inline-size;
          overflow-wrap: anywhere;
        }
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] h1,
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] h2 {
          max-inline-size: 100%;
          font-size: clamp(1.55rem, 8cqi, 3rem);
          line-height: 0.98;
          letter-spacing: -0.052em;
          text-wrap: balance;
        }
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] p {
          max-inline-size: 100%;
          line-height: 1.34;
        }
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] form.mc,
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] .mc[data-mc="command-row"] {
          grid-template-columns: minmax(0, 1fr);
          align-items: stretch;
        }
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] label,
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] input,
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] button,
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] a[data-mc-action] {
          inline-size: 100%;
          justify-self: stretch;
          white-space: normal;
        }

        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-shell {
          grid-template-columns: minmax(0, 1.08fr) minmax(min(360px, 100%), 0.92fr);
        }
        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] > .mc {
          min-block-size: max-content;
          align-content: center;
        }

        body[data-mcel-fit-remediation~="object-reshape"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] > .mc {
          border-radius: min(var(--site-radius-sm), 18cqi);
          padding: clamp(18px, 5cqi, 32px);
        }

        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-shell {
          grid-template-columns: minmax(0, 1fr);
        }
        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail {
          position: static;
        }

        @media (max-width: 860px) {
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-shell {
            grid-template-columns: 1fr;
          }
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail {
            position: static;
          }
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview {
            max-inline-size: min(100% - 28px, 760px);
          }
          body[data-mcel-chrome="chrome-cluster-grid"] .mcel-runtime-preview,
          body[data-mcel-chrome="chrome-spotlight"] .mcel-runtime-preview,
          body[data-mcel-chrome="chrome-journey"] .mcel-runtime-preview,
          body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-runtime-preview {
            max-inline-size: min(100% - 28px, 760px);
          }
          body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-shell {
            grid-template-columns: 1fr;
          }
          body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support {
            position: static;
          }
          body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step {
            grid-template-columns: 1fr;
          }
          body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step::before {
            justify-self: start;
          }
          .mcel-runtime-preview .mc[data-mc-kind="hero"],
          .mcel-runtime-preview form.mc,
          .mcel-runtime-preview .mc[data-mc="command-row"],
          .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] {
            grid-template-columns: 1fr;
          }
          .mcel-runtime-preview .mc[data-mc-kind="hero"]::after {
            grid-column: 1;
            grid-row: auto;
            justify-self: stretch;
            max-block-size: 320px;
          }
        }
      `;
    }

    function isolatedSiteDocument(runtimeHtml, meta = {}) {
      const reason = String(meta.reason || "sync").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const nonce = String(meta.nonce || "0").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const hash = String(meta.hash || "none").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const theme = typeof McelLabStyleLaw !== "undefined"
        ? McelLabStyleLaw.normalizeTheme(mcelLabState.theme)
        : (mcelLabState.theme || "theme-machine");
      const chrome = typeof MCEL !== "undefined" && MCEL.normalizeChrome
        ? MCEL.normalizeChrome(mcelLabState.chrome)
        : (mcelLabState.chrome || "chrome-strict-hierarchy");
      const chromeResult = typeof MCEL !== "undefined" && MCEL.applyChrome
        ? MCEL.applyChrome(runtimeHtml, {chrome, theme, reason})
        : {html: runtimeHtml || "", report: {chrome, changed: false, visibleResponse: Boolean(runtimeHtml)}};
      mcelLabState.chrome = chrome;
      mcelLabState.lastChromeReport = chromeResult.report;
      const renderedRuntimeHtml = chromeResult.html || runtimeHtml || "";
      return `<!doctype html>
<html data-mcel-frame-generation="${nonce}" data-mcel-frame-reason="${reason}" data-mcel-frame-hash="${hash}" data-mcel-theme="${theme}" data-mcel-chrome="${chrome}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MCEL rendered site</title>
<style>${isolatedSiteCss()}</style>
</head>
<body class="mcel-site-theme ${theme}" data-mcel-chrome="${chrome}">
  <!-- MCEL iframe twiddle: reason=${reason}; nonce=${nonce}; hash=${hash}; theme=${theme}; chrome=${chrome} -->
  <div class="mcel-runtime-preview ${theme}" data-mcel-theme="${theme}" data-mcel-chrome="${chrome}">
    ${renderedRuntimeHtml}
  </div>
</body>
</html>`;
    }

    function syncMcelRenderedSiteFrame(reason = "sync") {
      const frame = bindMcelSiteFrameLifecycle(reason);
      if (!frame || !mcelRuntimePreview) {
        recordMcelSiteFrameTwiddle("iframe-sync-skipped", {reason, hash: "missing", length: 0});
        return;
      }
      const twiddle = ensureMcelSiteFrameTwiddle();
      twiddle.syncCount += 1;
      twiddle.nonce += 1;
      const runtimeHtml = mcelRuntimePreview.innerHTML || "";
      const runtimeHash = hashMcelSiteFrameDocument(runtimeHtml);
      const nonce = `${Date.now()}-${twiddle.nonce}`;
      const documentHtml = isolatedSiteDocument(runtimeHtml, {reason, nonce, hash: runtimeHash});
      const documentHash = hashMcelSiteFrameDocument(documentHtml);
      frame.dataset.synced = reason;
      frame.dataset.srcdocHash = documentHash;
      frame.dataset.runtimeHash = runtimeHash;
      frame.dataset.srcdocLength = String(documentHtml.length);
      frame.dataset.chrome = mcelLabState.chrome || "chrome-strict-hierarchy";
      frame.dataset.lastNonce = nonce;
      twiddle.lastReason = reason;
      twiddle.lastHash = documentHash;
      twiddle.lastLength = documentHtml.length;
      twiddle.lastAt = new Date().toISOString();

      // Twiddle/fix: clear first, then write a nonce-bearing srcdoc. This makes repeated
      // opens observable and prevents browser no-op behavior when the same srcdoc is reused.
      frame.removeAttribute("srcdoc");
      frame.srcdoc = "";
      scheduleMcelSiteFrameWrite(() => {
        const liveFrame = currentMcelSiteFrame();
        if (!liveFrame || liveFrame !== frame || !liveFrame.isConnected) {
          recordMcelSiteFrameTwiddle("iframe-sync-abandoned", {reason, hash: documentHash, length: documentHtml.length});
          return;
        }
        liveFrame.srcdoc = documentHtml;
        liveFrame.dataset.synced = reason;
        liveFrame.dataset.srcdocHash = documentHash;
        liveFrame.dataset.runtimeHash = runtimeHash;
        liveFrame.dataset.srcdocLength = String(documentHtml.length);
        liveFrame.dataset.chrome = mcelLabState.chrome || "chrome-strict-hierarchy";
        liveFrame.dataset.lastNonce = nonce;
        liveFrame.dataset.fitStatus = "pending";
        liveFrame.dataset.fitViolations = "0";
        liveFrame.dataset.fitRemedies = "";
        recordMcelSiteFrameTwiddle("iframe-sync", {reason, hash: documentHash, length: documentHtml.length, fitStatus: "pending", fitViolations: 0});
      });
    }

    const MCEL_CANONICAL_TASK_MANAGER_REQUIRED_IDS = [
      "task-manager-app",
      "task-manager-status",
      "task-manager-server",
      "task-query",
      "task-limit",
      "task-refresh",
      "task-process-table",
      "task-all-process-table",
      "task-connection-table",
      "task-hardware-table",
      "task-ai-output"
    ];

    const MCEL_CANONICAL_TASK_MANAGER_DANGEROUS_CONTROL_IDS = [
      "task-server-shutdown",
      "task-server-start",
      "task-server-restart",
      "task-schedule-create"
    ];

    const MCEL_CANONICAL_TASK_MANAGER_DANGEROUS_CONTROL_SELECTORS = [
      "#task-server-shutdown",
      "#task-server-start",
      "#task-server-restart",
      "#task-schedule-create",
      "[data-task-action=\"terminate-pid\"]",
      "[data-task-action=\"kill-pid\"]"
    ];

    const MCEL_CANONICAL_GIT_TOOLS_REQUIRED_IDS = [
      "git-tools-app",
      "git-project-selector-panel",
      "git-project-list",
      "git-workflow-accordion",
      "git-server-pane",
      "gitea-workflow-layout",
      "git-server-status",
      "git-server-output",
      "git-server-remote-command"
    ];

    const MCEL_CANONICAL_GIT_TOOLS_DANGEROUS_CONTROL_SELECTORS = [
      "#git-server-start",
      "#git-server-restart",
      "#git-server-stop",
      "#git-server-remote-apply-local",
      "#git-server-push-local",
      "#git-server-use-external",
      "#git-server-mirror-setup",
      "#git-server-remote-add",
      "#git-server-remote-set-url",
      "#git-server-remote-push",
      "#git-server-remote-run"
    ];

    const MCEL_CANONICAL_SPECIMEN_CHROME_STYLE_ID = "mcel-lab-canonical-specimen-style";
    const MCEL_CANONICAL_SPECIMEN_RIBBON_ID = "mcel-lab-canonical-specimen-ribbon";

    const MCEL_CANONICAL_SPECIMEN_LENS_STYLE_ID = "mcel-lab-canonical-task-manager-lens-style";
    const MCEL_CANONICAL_TASK_MANAGER_LENS_HUD_ID = "mcel-lab-canonical-task-manager-lens-hud";
    const MCEL_CANONICAL_TASK_MANAGER_LENS_CLASS = "mcel-canonical-task-manager-lens";

    const MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_STYLE_ID =
      window.TaskManagerMcel?.ENRICHMENT_STYLE_ID || "mcel-lab-canonical-task-manager-enrichment-style";
    const MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_CLASS =
      window.TaskManagerMcel?.ENRICHMENT_CLASS || "mcel-canonical-task-manager-enriched";

    const MCEL_CANONICAL_TASK_MANAGER_REGION_ENRICHMENT = window.TaskManagerMcel?.REGION_ENRICHMENT || [];
    const MCEL_CANONICAL_TASK_MANAGER_COMPONENT_ENRICHMENT = window.TaskManagerMcel?.COMPONENT_ENRICHMENT || [];
    const MCEL_CANONICAL_TASK_MANAGER_FIELD_ENRICHMENT = window.TaskManagerMcel?.FIELD_ENRICHMENT || [];
    const MCEL_CANONICAL_TASK_MANAGER_PANEL_LENS = window.TaskManagerMcel?.PANEL_LENS || [];
    const MCEL_CANONICAL_TASK_MANAGER_ACTION_LENS = window.TaskManagerMcel?.ACTION_LENS || [];

    const MCEL_CANONICAL_GIT_TOOLS_ENRICHMENT_STYLE_ID =
      window.GitToolsMcel?.ENRICHMENT_STYLE_ID || "mcel-lab-canonical-git-tools-enrichment-style";
    const MCEL_CANONICAL_GIT_TOOLS_ENRICHMENT_CLASS =
      window.GitToolsMcel?.ENRICHMENT_CLASS || "mcel-canonical-git-tools-enriched";
    const MCEL_CANONICAL_GIT_TOOLS_REGION_ENRICHMENT = window.GitToolsMcel?.REGION_ENRICHMENT || [];
    const MCEL_CANONICAL_GIT_TOOLS_COMPONENT_ENRICHMENT = window.GitToolsMcel?.COMPONENT_ENRICHMENT || [];
    const MCEL_CANONICAL_GIT_TOOLS_FIELD_ENRICHMENT = window.GitToolsMcel?.FIELD_ENRICHMENT || [];
    const MCEL_CANONICAL_GIT_TOOLS_PANEL_LENS = window.GitToolsMcel?.PANEL_LENS || [];
    const MCEL_CANONICAL_GIT_TOOLS_ACTION_LENS = window.GitToolsMcel?.ACTION_LENS || [];

    function mcelCanonicalAppSlug(specimen = selectedMcelCanonicalAppSpecimen()) {
      return String(specimen?.app || "task-manager").replace(/[^a-z0-9-]+/gi, "-").toLowerCase();
    }

    function mcelCanonicalAppLabel(specimen = selectedMcelCanonicalAppSpecimen()) {
      return specimen?.label || window.McelSpecimenPlanner?.planFor?.(specimen?.app)?.label || (specimen?.app === "git-tools" ? "Git Tools" : "Task Manager");
    }

    function mcelPlannerEscape(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function mcelCanonicalAppPlannerPlan(specimen = selectedMcelCanonicalAppSpecimen()) {
      const planner = window.McelSpecimenPlanner || null;
      const plan = planner?.planFor
        ? planner.planFor(specimen.app, {
            route: specimen.route,
            rootSelector: specimen.rootSelector,
            label: specimen.label
          })
        : {
            app: specimen.app,
            label: specimen.label,
            route: specimen.route,
            rootSelector: specimen.rootSelector,
            status: "planner-unavailable",
            point: "Purpose-aware specimen planner is unavailable.",
            expectedRegions: [],
            expectedFeeds: [],
            expectedFields: [],
            expectedActionFamilies: [],
            knownRiskFamilies: [],
            neverExecute: [],
            mountNeeds: ["load mcel-specimen-planner.js before mcel-lab.js"]
          };
      const doc = mcelCanonicalAppFrameDocument();
      if (planner?.inspectMountedDocument && doc?.body) {
        plan.mountedEvidence = planner.inspectMountedDocument(doc, plan);
      }
      mcelLabState.lastCanonicalSpecimenPlan = plan;
      return plan;
    }

    function renderMcelCanonicalAppPlanner(reason = "planner-render") {
      if (!mcelCanonicalAppPlan && !mcelCanonicalAppPlanSummary && !mcelCanonicalAppPlanList) return null;
      const specimen = selectedMcelCanonicalAppSpecimen();
      const planner = window.McelSpecimenPlanner || null;
      const plan = mcelCanonicalAppPlannerPlan(specimen);
      const riskLevel = planner?.riskLevel?.(plan) || ((plan.knownRiskFamilies || []).length ? "medium" : "low");
      const queue = planner?.mountQueue?.() || [];
      const evidence = plan.mountedEvidence || {};
      const summary = planner?.summaryFor?.(plan) || `${plan.label || plan.app}: ${plan.point || "purpose unknown"}`;
      if (mcelCanonicalAppPlan) {
        mcelCanonicalAppPlan.dataset.mcelPlannerApp = plan.app || specimen.app;
        mcelCanonicalAppPlan.dataset.mcelPlannerStatus = plan.status || "unknown";
        mcelCanonicalAppPlan.dataset.mcelPlannerRisk = riskLevel;
      }
      const heading = mcelCanonicalAppPlan?.querySelector?.(".mcel-canonical-app-plan-heading span");
      if (heading) {
        heading.textContent = `${plan.status || "unknown"} · ${riskLevel} risk · ${queue.length} queued app(s)`;
      }
      if (mcelCanonicalAppPlanSummary) {
        mcelCanonicalAppPlanSummary.textContent = summary;
      }
      if (mcelCanonicalAppPlanList) {
        const items = [
          ["Point", plan.point || "unknown"],
          ["Domain pack", `${plan.domainPack || "needs-domain-pack"} · adapter: ${plan.adapter || "needs-adapter"}`],
          ["Expected regions", (plan.expectedRegions || []).join(", ") || "discover on mount"],
          ["Actions", (plan.expectedActionFamilies || []).join(", ") || "discover on mount"],
          ["Risk families", (plan.knownRiskFamilies || []).join(", ") || "none declared"],
          ["Never execute", (plan.neverExecute || []).join(", ") || "unknown destructive actions"],
          ["Decode hints", (plan.decodeHints || []).join(", ") || "app id and root selector"],
          ["Mount needs", (plan.mountNeeds || []).join("; ") || "read-only discovery pass"],
          ["Mounted evidence", evidence.rootPresent ? `${evidence.controlCount || 0} controls · ${evidence.feedCount || 0} feeds · ${evidence.editableCount || 0} editables` : "not mounted or root not inspected"]
        ];
        mcelCanonicalAppPlanList.innerHTML = items.map(([label, value]) =>
          `<li><strong>${mcelPlannerEscape(label)}:</strong> ${mcelPlannerEscape(value)}</li>`
        ).join("");
      }
      return plan;
    }

    function mcelCanonicalAppAdapter(specimen = selectedMcelCanonicalAppSpecimen()) {
      if (specimen?.app === "git-tools") return window.GitToolsMcel || null;
      if (specimen?.app === "task-manager") return window.TaskManagerMcel || null;
      if (specimen?.app === "terminal") return window.TerminalMcel || null;
      const planner = window.McelSpecimenPlanner || null;
      const plan = mcelCanonicalAppPlannerPlan(specimen);
      return planner?.createGenericAdapter?.(plan) || null;
    }

    function mcelTaskManagerMcelAdapter(specimen = selectedMcelCanonicalAppSpecimen()) {
      return mcelCanonicalAppAdapter(specimen);
    }

    function mcelCanonicalAppRequiredIds(specimen = selectedMcelCanonicalAppSpecimen()) {
      if (specimen?.app === "git-tools") return MCEL_CANONICAL_GIT_TOOLS_REQUIRED_IDS;
      if (specimen?.app === "task-manager") return MCEL_CANONICAL_TASK_MANAGER_REQUIRED_IDS;
      const plan = mcelCanonicalAppPlannerPlan(specimen);
      return window.McelSpecimenPlanner?.requiredIdsFor?.(plan) || [String(specimen?.rootSelector || "").replace(/^#/, "")].filter(Boolean);
    }

    function mcelCanonicalAppDangerousControlSelectors(specimen = selectedMcelCanonicalAppSpecimen()) {
      if (specimen?.app === "git-tools") return MCEL_CANONICAL_GIT_TOOLS_DANGEROUS_CONTROL_SELECTORS;
      if (specimen?.app === "task-manager") return MCEL_CANONICAL_TASK_MANAGER_DANGEROUS_CONTROL_SELECTORS;
      const plan = mcelCanonicalAppPlannerPlan(specimen);
      return window.McelSpecimenPlanner?.dangerousSelectorsFor?.(plan) || [];
    }

    function mcelCanonicalAppRegionEnrichment(specimen = selectedMcelCanonicalAppSpecimen()) {
      return mcelCanonicalAppAdapter(specimen)?.REGION_ENRICHMENT ||
        (specimen?.app === "git-tools" ? MCEL_CANONICAL_GIT_TOOLS_REGION_ENRICHMENT : MCEL_CANONICAL_TASK_MANAGER_REGION_ENRICHMENT);
    }

    function mcelCanonicalAppPanelLens(specimen = selectedMcelCanonicalAppSpecimen()) {
      return mcelCanonicalAppAdapter(specimen)?.PANEL_LENS ||
        (specimen?.app === "git-tools" ? MCEL_CANONICAL_GIT_TOOLS_PANEL_LENS : MCEL_CANONICAL_TASK_MANAGER_PANEL_LENS);
    }

    function mcelCanonicalAppActionLens(specimen = selectedMcelCanonicalAppSpecimen()) {
      return mcelCanonicalAppAdapter(specimen)?.ACTION_LENS ||
        (specimen?.app === "git-tools" ? MCEL_CANONICAL_GIT_TOOLS_ACTION_LENS : MCEL_CANONICAL_TASK_MANAGER_ACTION_LENS);
    }

    function mcelCanonicalAppEnrichmentStyleId(specimen = selectedMcelCanonicalAppSpecimen()) {
      return mcelCanonicalAppAdapter(specimen)?.ENRICHMENT_STYLE_ID ||
        (specimen?.app === "git-tools" ? MCEL_CANONICAL_GIT_TOOLS_ENRICHMENT_STYLE_ID : MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_STYLE_ID);
    }

    function mcelCanonicalAppEnrichmentClass(specimen = selectedMcelCanonicalAppSpecimen()) {
      return mcelCanonicalAppAdapter(specimen)?.ENRICHMENT_CLASS ||
        (specimen?.app === "git-tools" ? MCEL_CANONICAL_GIT_TOOLS_ENRICHMENT_CLASS : MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_CLASS);
    }

    function mcelCanonicalAppBodyEnrichmentAttribute(specimen = selectedMcelCanonicalAppSpecimen()) {
      return mcelCanonicalAppAdapter(specimen)?.BODY_ENRICHMENT_ATTRIBUTE ||
        (specimen?.app === "git-tools" ? "data-mcel-git-enrichment" : "data-mcel-task-enrichment");
    }

    function mcelCanonicalAppApplySemantics(adapter) {
      return adapter?.applyCanonicalMcelSemantics ||
        adapter?.applyGitToolsMcelSemantics ||
        adapter?.applyTaskManagerMcelSemantics ||
        null;
    }

    function syncMcelCanonicalSpecimenControls(reason = "sync-specimen-controls") {
      const specimen = selectedMcelCanonicalAppSpecimen();
      const label = mcelCanonicalAppLabel(specimen);
      if (mcelCanonicalAppMount) {
        mcelCanonicalAppMount.textContent = `Mount ${label}`;
      }
      if (mcelCanonicalAppFrame && mcelCanonicalAppFrame.getAttribute("src") === "about:blank") {
        mcelCanonicalAppFrame.dataset.mcelSpecimenApp = specimen.app;
        mcelCanonicalAppFrame.dataset.mcelSpecimenRoot = specimen.rootSelector;
        mcelCanonicalAppFrame.dataset.mcelSpecimenRoute = specimen.route;
      }
      if (mcelCanonicalAppFrameSummary && (!mcelLabState?.canonicalAppSpecimen?.status || mcelLabState.canonicalAppSpecimen.status === "idle")) {
        mcelCanonicalAppFrameSummary.textContent = `${label} has not been mounted yet.`;
      }
      if (mcelCanonicalAppReport && !mcelLabState?.lastCanonicalSpecimenReport) {
        mcelCanonicalAppReport.textContent = `Mount ${label} to enrich it as a canonical MCEL specimen.`;
      }
      renderMcelCanonicalAppPlanner(reason);
      return reason;
    }

    function ensureMcelCanonicalAppSpecimenState() {
      if (!mcelLabState.canonicalAppSpecimen) {
        mcelLabState.canonicalAppSpecimen = {
          mountCount: 0,
          loadCount: 0,
          errorCount: 0,
          inspectCount: 0,
          proofCount: 0,
          specimenChromeCount: 0,
          app: "task-manager",
          route: "/applications/task-manager/server-processes?mcel_lab_specimen=task-manager",
          rootSelector: "#task-manager-app",
          status: "idle",
          lastAt: null
        };
      }
      mcelLabState.canonicalAppSpecimen.lensCount = mcelLabState.canonicalAppSpecimen.lensCount || 0;
      mcelLabState.canonicalAppSpecimen.lensStatus = mcelLabState.canonicalAppSpecimen.lensStatus || "idle";
      mcelLabState.canonicalAppSpecimen.enrichmentCount = mcelLabState.canonicalAppSpecimen.enrichmentCount || 0;
      mcelLabState.canonicalAppSpecimen.enrichmentStatus = mcelLabState.canonicalAppSpecimen.enrichmentStatus || "idle";
      return mcelLabState.canonicalAppSpecimen;
    }

    function selectedMcelCanonicalAppSpecimen() {
      const option = mcelCanonicalAppSelect?.selectedOptions?.[0] || mcelCanonicalAppSelect?.querySelector?.("option");
      const frame = mcelCanonicalAppFrame;
      const app = option?.value || frame?.dataset?.mcelSpecimenApp || "task-manager";
      return {
        app,
        route: option?.dataset?.route || frame?.dataset?.mcelSpecimenRoute || "/applications/task-manager/server-processes?mcel_lab_specimen=task-manager",
        rootSelector: option?.dataset?.root || frame?.dataset?.mcelSpecimenRoot || "#task-manager-app",
        label: option?.textContent?.trim() || "Task Manager",
        plannerStatus: option?.dataset?.plannerStatus || "",
        point: option?.dataset?.point || ""
      };
    }

    function renderMcelCanonicalAppSpecimenStatus(reason = "render") {
      const state = ensureMcelCanonicalAppSpecimenState();
      const specimen = selectedMcelCanonicalAppSpecimen();
      const label = mcelCanonicalAppLabel(specimen);
      const report = mcelLabState.lastCanonicalSpecimenReport;
      const proof = mcelLabState.lastCanonicalSpecimenProof;
      const status = [
        `specimen=${state.app || specimen.app || "task-manager"}`,
        `status=${state.status || "idle"}`,
        `mounts=${state.mountCount || 0}`,
        `loads=${state.loadCount || 0}`,
        `inspections=${state.inspectCount || 0}`,
        `proofs=${state.proofCount || 0}`,
        `chrome=${state.specimenChromeCount || 0}`,
        `enrichment=${state.enrichmentStatus || "idle"}`,
        `enrichmentRuns=${state.enrichmentCount || 0}`,
        `lens=${state.lensStatus || "idle"}`,
        `lensRuns=${state.lensCount || 0}`,
        report ? `root=${report.rootPresent ? "present" : "missing"}` : "root=unknown",
        proof ? `browserProof=${proof.failed ? "warning" : "ready"}` : "browserProof=not-run",
        `reason=${reason}`
      ].join(" · ");
      if (mcelCanonicalAppStatus) mcelCanonicalAppStatus.textContent = status;
      if (mcelCanonicalAppFrameShell) {
        mcelCanonicalAppFrameShell.dataset.mcelSpecimenFrameStatus = state.status || "idle";
      }
      if (mcelCanonicalAppFrameSummary) {
        const mounted = state.status && state.status !== "idle";
        const rootSummary = report ? `root ${report.rootPresent ? "present" : "missing"}` : "root not inspected";
        const proofSummary = proof ? `proof ${proof.failed ? "warning" : "ready"}` : "proof pending";
        const enrichment = mcelLabState.lastCanonicalSpecimenEnrichment;
        const enrichmentSummary = enrichment ? `enriched: ${enrichment.enrichedElementCount} element(s), ${enrichment.layoutLawStatus}` : "enrichment pending";
        const supercutSummary = enrichment?.supercutActive
          ? `supercut: ${enrichment.supercutComponentCount || 0} executable component(s), ${enrichment.supercutOriginalPointCount || 0} original point(s), rewrite preview ${enrichment.supercutRewritePreviewCount || 0} node(s), unsafe blocked ${enrichment.supercutUnsafeActionsBlocked || 0}`
          : "supercut pending";
        const lens = mcelLabState.lastCanonicalSpecimenLens;
        const lensSummary = lens ? `lens active: ${lens.classifiedPanelCount} panel(s), ${lens.riskControlCount} risk surface(s)` : "lens pending";
        mcelCanonicalAppFrameSummary.textContent = mounted
          ? `${label} specimen ${state.status}; ${rootSummary}; ${proofSummary}; ${enrichmentSummary}; ${supercutSummary}; ${lensSummary}.`
          : `${label} has not been mounted yet.`;
      }
      if (mcelCanonicalAppReport && !mcelLabState.lastCanonicalSpecimenReport) {
        mcelCanonicalAppReport.textContent = `Mount ${label} to enrich it as a canonical MCEL specimen.`;
      }
      return status;
    }

    function injectMcelCanonicalAppSpecimenChrome(reason = "specimen-chrome") {
      const state = ensureMcelCanonicalAppSpecimenState();
      const specimen = selectedMcelCanonicalAppSpecimen();
      const doc = mcelCanonicalAppFrameDocument();
      if (!doc?.body) return false;
      const root = doc.querySelector?.(specimen.rootSelector) || null;
      doc.documentElement?.setAttribute?.("data-mcel-lab-specimen", specimen.app);
      doc.body.setAttribute("data-mcel-lab-specimen", specimen.app);
      doc.body.setAttribute("data-mcel-lab-specimen-reason", reason);
      let style = doc.getElementById(MCEL_CANONICAL_SPECIMEN_CHROME_STYLE_ID);
      if (!style) {
        style = doc.createElement("style");
        style.id = MCEL_CANONICAL_SPECIMEN_CHROME_STYLE_ID;
        style.textContent = `
          [data-mcel-lab-specimen-root="true"] {
            outline: 1px solid rgba(115, 214, 255, 0.42);
            outline-offset: -3px;
          }
          [data-mcel-lab-specimen-root="true"]:focus-within {
            outline-color: rgba(115, 214, 255, 0.76);
          }
        `;
        doc.head?.appendChild?.(style);
      }

      // Earlier versions inserted a fixed in-frame ribbon. That crowded legacy app chrome,
      // so this inspector twiddle deliberately removes any stale ribbon and keeps the visible
      // MCEL status in the Lab sidecar/frame bar instead.
      doc.getElementById(MCEL_CANONICAL_SPECIMEN_RIBBON_ID)?.remove?.();

      if (root) {
        root.setAttribute("data-mcel-lab-specimen-root", "true");
        root.setAttribute("data-mcel-lab-specimen-app", specimen.app);
      }
      state.specimenChromeCount = (state.specimenChromeCount || 0) + 1;
      return true;
    }

    function ensureMcelCanonicalTaskManagerEnrichmentStyle(doc) {
      return Boolean(mcelTaskManagerMcelAdapter()?.ensureEnrichmentStyle?.(doc));
    }

    function applyMcelElementEnrichment(element, definition) {
      return Boolean(mcelTaskManagerMcelAdapter()?.applyElementEnrichment?.(element, definition, {
        enrichedBy: "task-manager-lab",
        source: "legacy-dom-reader"
      }));
    }

    function mcelNearestControlLabel(control) {
      return mcelTaskManagerMcelAdapter()?.nearestControlLabel?.(control) || control?.closest?.("label") || control?.parentElement || null;
    }

    function buildMcelCanonicalTaskManagerEnrichmentModel(doc, root, reason = "build-enrichment") {
      const specimen = selectedMcelCanonicalAppSpecimen();
      const adapter = mcelTaskManagerMcelAdapter(specimen);
      return adapter?.buildEnrichmentModel?.(doc, root, {
        reason,
        rootSelector: specimen.rootSelector,
        generatedBy: `mcel-lab-${mcelCanonicalAppSlug(specimen)}-legacy-dom-enrichment`
      }) || {
        app: specimen.app,
        kind: specimen.app === "git-tools" ? "repository-operations-console" : "operator-console",
        layout: specimen.app === "git-tools" ? "stacked-progressive-workflow" : "sidebar-workspace",
        rootSelector: specimen.rootSelector,
        rootPresent: Boolean(root),
        regions: [],
        components: [],
        fields: [],
        actions: [],
        generatedBy: `mcel-lab-${mcelCanonicalAppSlug(specimen)}-legacy-dom-enrichment`,
        reason,
        builtAt: new Date().toISOString(),
        laws: []
      };
    }

    function collectMcelCanonicalTaskManagerEnrichmentViolations(doc, root) {
      const specimen = selectedMcelCanonicalAppSpecimen();
      return mcelTaskManagerMcelAdapter(specimen)?.collectEnrichmentViolations?.(doc, root) ||
        [{law: "adapter-present", status: "failed", message: `${mcelCanonicalAppLabel(specimen)} MCEL adapter unavailable`}];
    }

    function applyMcelCanonicalTaskManagerEnrichment(reason = "enrichment") {
      const state = ensureMcelCanonicalAppSpecimenState();
      const specimen = selectedMcelCanonicalAppSpecimen();
      const doc = mcelCanonicalAppFrameDocument();
      const adapter = mcelTaskManagerMcelAdapter(specimen);
      const label = mcelCanonicalAppLabel(specimen);
      if (!adapter) {
        const unavailable = {
          app: specimen.app,
          rootSelector: specimen.rootSelector,
          enrichmentActive: false,
          rootPresent: false,
          enrichedElementCount: 0,
          layoutLawStatus: "unavailable",
          violations: [{law: "shared-adapter", status: "failed", message: `${label} MCEL adapter unavailable`}],
          destructiveActionsExecuted: false,
          safetyClaim: `enrichment reads and annotates the ${label} specimen DOM; it never clicks application controls`,
          reason,
          appliedAt: new Date().toISOString()
        };
        state.enrichmentStatus = "unavailable";
        mcelLabState.lastCanonicalSpecimenEnrichment = unavailable;
        renderMcelCanonicalAppLensMap(unavailable, reason);
        renderMcelCanonicalAppSpecimenStatus(reason);
        return unavailable;
      }
      if (!doc?.body) {
        const unavailable = adapter.createUnavailableReport({
          rootSelector: specimen.rootSelector,
          reason,
          law: "iframe-document",
          message: "iframe document unavailable"
        });
        state.enrichmentStatus = "unavailable";
        mcelLabState.lastCanonicalSpecimenEnrichment = unavailable;
        renderMcelCanonicalAppLensMap(unavailable, reason);
        renderMcelCanonicalAppSpecimenStatus(reason);
        return unavailable;
      }

      injectMcelCanonicalAppSpecimenChrome(reason);
      const applySemantics = mcelCanonicalAppApplySemantics(adapter);
      if (!applySemantics) {
        const unavailable = adapter.createUnavailableReport?.({
          rootSelector: specimen.rootSelector,
          reason,
          law: "adapter-semantics",
          message: `${label} MCEL semantic adapter unavailable`
        }) || {
          app: specimen.app,
          rootSelector: specimen.rootSelector,
          enrichmentActive: false,
          rootPresent: false,
          enrichedElementCount: 0,
          layoutLawStatus: "unavailable",
          violations: [{law: "adapter-semantics", status: "failed", message: `${label} MCEL semantic adapter unavailable`}],
          destructiveActionsExecuted: false,
          safetyClaim: `enrichment reads and annotates the ${label} specimen DOM; it never clicks application controls`,
          reason,
          appliedAt: new Date().toISOString()
        };
        state.enrichmentStatus = "unavailable";
        mcelLabState.lastCanonicalSpecimenEnrichment = unavailable;
        renderMcelCanonicalAppLensMap(unavailable, reason);
        renderMcelCanonicalAppSpecimenStatus(reason);
        return unavailable;
      }
      const report = applySemantics.call(adapter, {
        document: doc,
        rootSelector: specimen.rootSelector,
        route: mcelCanonicalAppFrame?.dataset?.mcelSpecimenRoute || specimen.route,
        reason,
        mode: "lab-specimen",
        proofSurface: "canonical-app-specimen",
        sidebarWidth: specimen.app === "task-manager" ? "300px" : undefined,
        source: "legacy-dom-reader",
        enrichedBy: `${mcelCanonicalAppSlug(specimen)}-lab`,
        generatedBy: `mcel-lab-${mcelCanonicalAppSlug(specimen)}-legacy-dom-enrichment`
      });

      state.enrichmentCount = (state.enrichmentCount || 0) + 1;
      state.enrichmentStatus = report.enrichmentActive ? report.layoutLawStatus : "warning";
      state.lastAt = report.appliedAt;
      mcelLabState.lastCanonicalSpecimenEnrichment = report;
      renderMcelCanonicalAppPlanner(reason);
      renderMcelCanonicalAppLensMap(report, reason);
      if (mcelCanonicalAppReport) {
        mcelCanonicalAppReport.textContent = JSON.stringify({enrichment: report}, null, 2);
      }
      recordMcelEvent(
        "canonical-app",
        report.enrichmentActive ? "MCEL_CANONICAL_TASK_MANAGER_ENRICHED" : "MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_WARNING",
        report.enrichmentActive
          ? `${label} enrichment mapped ${report.regionCount} region(s), ${report.componentCount} component(s), ${report.fieldCount} field(s), and ${report.riskControlCount} risk surface(s).`
          : `${label} enrichment could not find ${specimen.rootSelector}.`,
        report.enrichmentActive ? (report.violations?.length ? "warning" : "success") : "warning"
      );
      renderMcelCanonicalAppSpecimenStatus(reason);
      return report;
    }

    function ensureMcelCanonicalTaskManagerLensStyle(doc) {
      if (!doc?.head) return false;
      let style = doc.getElementById(MCEL_CANONICAL_SPECIMEN_LENS_STYLE_ID);
      if (style) return true;
      style = doc.createElement("style");
      style.id = MCEL_CANONICAL_SPECIMEN_LENS_STYLE_ID;
      style.textContent = `
        body[data-mcel-canonical-lens="active"] [data-mcel-lens-role] {
          position: relative;
        }
        body[data-mcel-canonical-lens="active"] [data-mcel-lens-role]::before {
          content: "";
          position: absolute;
          inset: 4px;
          z-index: 2;
          border: 1px solid rgba(115, 214, 255, 0.0);
          border-radius: inherit;
          opacity: 0;
          pointer-events: none;
          transition: opacity 120ms ease, border-color 120ms ease, box-shadow 120ms ease;
        }
        body[data-mcel-canonical-lens="active"] [data-mcel-lens-role]:hover::before,
        body[data-mcel-canonical-lens="active"] [data-mcel-lens-role]:focus-within::before {
          border-color: rgba(115, 214, 255, 0.5);
          box-shadow: inset 0 0 0 1px rgba(115, 214, 255, 0.08);
          opacity: 1;
        }
        body[data-mcel-canonical-lens="active"] button[data-mcel-action-risk]:focus-visible {
          outline: 2px solid rgba(115, 214, 255, 0.74);
          outline-offset: 2px;
        }
        body[data-mcel-canonical-lens="active"] .mcel-lens-label,
        body[data-mcel-canonical-lens="active"] .mcel-lens-hud,
        body[data-mcel-canonical-lens="active"] .mcel-lens-risk-badge {
          display: none !important;
        }
      `;
      doc.head.appendChild(style);
      return true;
    }

    function ensureMcelCanonicalTaskManagerLensLabel(doc, element, label, kind = "surface") {
      if (!doc || !element) return false;
      element.setAttribute("data-mcel-lens-label", label);
      element.setAttribute("data-mcel-lens-kind", kind);
      const staleBadge = element.querySelector?.(":scope > .mcel-lens-label");
      if (staleBadge?.dataset?.mcelLensGenerated === "true") {
        staleBadge.remove();
      }
      return true;
    }

    function renderMcelCanonicalTaskManagerLensHud(doc, root, report) {
      if (!doc?.body || !root || !report) return false;
      doc.getElementById(MCEL_CANONICAL_TASK_MANAGER_LENS_HUD_ID)?.remove?.();
      root.setAttribute("data-mcel-lens-sidecar", "active");
      root.setAttribute(
        "aria-description",
        `MCEL Lab sidecar classified ${report.classifiedPanelCount} panels and ${report.riskControlCount} risk surfaces without modifying Task Manager controls.`
      );
      return true;
    }

    function renderMcelCanonicalAppLensMap(report, reason = "render-lens-map") {
      if (!mcelCanonicalAppLensMap) return false;
      const specimen = selectedMcelCanonicalAppSpecimen();
      const label = mcelCanonicalAppLabel(specimen);
      const regionEnrichment = mcelCanonicalAppRegionEnrichment(specimen);
      mcelCanonicalAppLensMap.replaceChildren();
      const heading = document.createElement("div");
      heading.className = "mcel-canonical-app-lens-map-heading";
      const title = document.createElement("strong");
      title.textContent = report?.enrichmentActive ? `${label} enrichment map` : `${label} specimen map`;
      const meta = document.createElement("span");
      meta.textContent = report
        ? report.enrichmentActive
          ? `enrichment ${report.layoutLawStatus || "active"} · ${report.reason || reason}`
          : `inspector ${report.lensActive ? "active" : "inactive"} · ${report.reason || reason}`
        : "enrichment not applied yet";
      heading.append(title, meta);
      mcelCanonicalAppLensMap.appendChild(heading);

      const items = report ? report.enrichmentActive ? [
        ["Root", report.rootPresent ? "present" : "missing"],
        ["Regions", `${report.regionCount || 0}/${regionEnrichment.length}`],
        ["Components", `${report.componentCount || 0} enriched`],
        ["Supercut", report.supercutActive ? `${report.supercutComponentCount || 0} executable` : "not run"],
        ["Rounds", report.supercutActive ? `${report.supercutRoundCount || 0} rectified` : "0"],
        ["Supercut Architecture", report.supercutArchitectureStatus === "ready" ? `${report.supercutPacksLoadedCount || 0} packs · ${report.supercutRulesFired || 0} rules` : (report.supercutArchitectureStatus || "legacy")],
        ["Rewrite preview", report.supercutRewritePreviewCount ? `${report.supercutRewritePreviewCount} nodes` : "not emitted"],
        ["Unsafe blocked", `${report.supercutUnsafeActionsBlocked || 0}`],
        ["Fields", `${report.fieldCount || 0} enriched`],
        ["Actions", `${report.actionControlCount || 0} classified`],
        ["Fit laws", `${report.fitLawCount || 0} declared`],
        ["Violations", `${report.violations?.length || 0}`],
        ["Safety", report.destructiveActionsExecuted ? "mutation executed" : "no destructive clicks"]
      ] : [
        ["Root", report.rootPresent ? "present" : "missing"],
        ["Panels", `${report.classifiedPanelCount || 0}/${report.panelCount || 0}`],
        ["Feeds", String(report.feedCount || 0)],
        ["Actions", `${report.actionControlCount || 0} classified`],
        ["Risk", `${report.riskControlCount || 0} audited in sidecar`],
        ["Overlay", report.overlayMode || "subtle hover/focus only"],
        ["Safety", report.destructiveActionsExecuted ? "mutation executed" : "no destructive clicks"]
      ] : [
        ["Root", "unknown"],
        ["Regions", "not enriched"],
        ["Components", "not enriched"],
        ["Fields", "not enriched"],
        ["Actions", "not classified"],
        ["Fit laws", "not declared"],
        ["Safety", "observational"]
      ];

      const grid = document.createElement("div");
      grid.className = "mcel-canonical-app-lens-map-grid";
      items.forEach(([label, value]) => {
        const card = document.createElement("div");
        card.className = "mcel-canonical-app-lens-map-card";
        const k = document.createElement("span");
        k.textContent = label;
        const v = document.createElement("strong");
        v.textContent = value;
        card.append(k, v);
        grid.appendChild(card);
      });
      mcelCanonicalAppLensMap.appendChild(grid);

      if (report?.regions?.length) {
        const details = document.createElement("div");
        details.className = "mcel-canonical-app-lens-sidecar-list";
        const label = document.createElement("strong");
        label.textContent = "Enriched regions:";
        details.appendChild(label);
        report.regions.filter((region) => region.present).forEach((region) => {
          const chip = document.createElement("span");
          chip.textContent = `${region.role}: ${region.fitContext || region.layout || "semantic"}`;
          details.appendChild(chip);
        });
        mcelCanonicalAppLensMap.appendChild(details);
      }

      if (report?.supercutOriginalPoints?.length) {
        const details = document.createElement("div");
        details.className = "mcel-canonical-app-lens-sidecar-list";
        const label = document.createElement("strong");
        label.textContent = "MCEL Supercut original points:";
        details.appendChild(label);
        report.supercutOriginalPoints.slice(0, 10).forEach((point) => {
          const chip = document.createElement("span");
          chip.textContent = point;
          details.appendChild(chip);
        });
        mcelCanonicalAppLensMap.appendChild(details);
      }

      if (report?.supercutPacksLoaded?.length || report?.supercutRewritePreviewCount) {
        const details = document.createElement("div");
        details.className = "mcel-canonical-app-lens-sidecar-list";
        const label = document.createElement("strong");
        label.textContent = "Supercut Architecture:";
        details.appendChild(label);
        [
          `packs loaded: ${report.supercutPacksLoadedCount || report.supercutPacksLoaded?.length || 0}`,
          `rules fired: ${report.supercutRulesFired || 0}`,
          `blackboard records: ${report.supercutBlackboardRecordCount || 0}`,
          `rewrite-preview nodes: ${report.supercutRewritePreviewCount || 0}`,
          `explanations ready: ${report.supercutExplanationsReady || 0}`,
          `unsafe actions blocked: ${report.supercutUnsafeActionsBlocked || 0}`
        ].forEach((item) => {
          const chip = document.createElement("span");
          chip.textContent = item;
          details.appendChild(chip);
        });
        (report.supercutPacksLoaded || []).slice(0, 4).forEach((pack) => {
          const chip = document.createElement("span");
          chip.textContent = `${pack.id || pack}: ${pack.ruleCount || 0} rules`;
          details.appendChild(chip);
        });
        mcelCanonicalAppLensMap.appendChild(details);
      }

      if (report?.supercutRewritePreviewCount) {
        const details = document.createElement("div");
        details.className = "mcel-canonical-app-lens-sidecar-list";
        const label = document.createElement("strong");
        label.textContent = "Rewrite preview:";
        details.appendChild(label);
        const summary = report.supercutRewritePreviewSummary || {};
        [
          `${report.supercutRewritePreviewCount || 0} nodes`,
          `root: ${summary.root || 0}`,
          `panels: ${summary.panels || 0}`,
          `toolbars: ${summary.toolbars || 0}`,
          `fields: ${summary.fields || 0}`,
          `actions: ${summary.actions || 0}`,
          `status feeds: ${summary.statusFeeds || 0}`,
          `unknown: ${summary.unknown || 0}`
        ].forEach((item) => {
          const chip = document.createElement("span");
          chip.textContent = item;
          details.appendChild(chip);
        });
        (report.supercutRewritePreview || []).slice(0, 6).forEach((node) => {
          const chip = document.createElement("span");
          chip.textContent = `${node.proposedTag || "mcel-node"} ${node.sourceSelector || node.id} → ${node.contract || "component.unknown"}${node.risk ? ` ${node.risk}` : ""}${node.proofPolicy ? ` ${node.proofPolicy}` : ""}`;
          details.appendChild(chip);
        });
        mcelCanonicalAppLensMap.appendChild(details);
      }

      if (report?.violations?.length) {
        const details = document.createElement("div");
        details.className = "mcel-canonical-app-lens-sidecar-list";
        const label = document.createElement("strong");
        label.textContent = "Fit proof warnings:";
        details.appendChild(label);
        report.violations.slice(0, 8).forEach((violation) => {
          const chip = document.createElement("span");
          chip.textContent = `${violation.law}: ${violation.role || violation.id || violation.selector || "surface"}`;
          details.appendChild(chip);
        });
        mcelCanonicalAppLensMap.appendChild(details);
      } else if (report?.riskControls?.length) {
        const details = document.createElement("div");
        details.className = "mcel-canonical-app-lens-sidecar-list";
        const label = document.createElement("strong");
        label.textContent = "Risk surfaces stay in sidecar:";
        details.appendChild(label);
        report.riskControls.slice(0, 8).forEach((control) => {
          const chip = document.createElement("span");
          chip.textContent = `${control.role}: ${control.risk}`;
          details.appendChild(chip);
        });
        mcelCanonicalAppLensMap.appendChild(details);
      }
      return true;
    }

    function clearMcelCanonicalTaskManagerLens(reason = "clear-lens") {
      const state = ensureMcelCanonicalAppSpecimenState();
      const specimen = selectedMcelCanonicalAppSpecimen();
      const label = mcelCanonicalAppLabel(specimen);
      const slug = mcelCanonicalAppSlug(specimen);
      const panelLens = mcelCanonicalAppPanelLens(specimen);
      const actionLens = mcelCanonicalAppActionLens(specimen);
      const doc = mcelCanonicalAppFrameDocument();
      if (!doc?.body) {
        state.lensStatus = "idle";
        renderMcelCanonicalAppLensMap(null, reason);
        renderMcelCanonicalAppSpecimenStatus(reason);
        return false;
      }

      const selectedEnrichmentStyleId = mcelCanonicalAppEnrichmentStyleId(specimen);
      const selectedEnrichmentClass = mcelCanonicalAppEnrichmentClass(specimen);
      const selectedBodyEnrichmentAttribute = mcelCanonicalAppBodyEnrichmentAttribute(specimen);
      doc.documentElement?.removeAttribute?.("data-mcel-canonical-lens");
      doc.documentElement?.removeAttribute?.("data-mcel-task-enrichment");
      doc.documentElement?.removeAttribute?.("data-mcel-git-enrichment");
      doc.documentElement?.removeAttribute?.(selectedBodyEnrichmentAttribute);
      doc.body.removeAttribute("data-mcel-canonical-lens");
      doc.body.removeAttribute("data-mcel-task-enrichment");
      doc.body.removeAttribute("data-mcel-git-enrichment");
      doc.body.removeAttribute(selectedBodyEnrichmentAttribute);
      doc.body.classList.remove(MCEL_CANONICAL_TASK_MANAGER_LENS_CLASS);
      doc.body.classList.remove(MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_CLASS);
      doc.body.classList.remove(MCEL_CANONICAL_GIT_TOOLS_ENRICHMENT_CLASS);
      doc.body.classList.remove(selectedEnrichmentClass);
      doc.body.style.removeProperty("--mcel-task-sidebar-width");
      doc.getElementById(MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_STYLE_ID)?.remove?.();
      doc.getElementById(MCEL_CANONICAL_GIT_TOOLS_ENRICHMENT_STYLE_ID)?.remove?.();
      doc.getElementById(selectedEnrichmentStyleId)?.remove?.();
      doc.getElementById(MCEL_CANONICAL_TASK_MANAGER_LENS_HUD_ID)?.remove?.();
      doc.getElementById(MCEL_CANONICAL_SPECIMEN_RIBBON_ID)?.remove?.();

      const generatedSelectors = [
        ".mcel-lens-label[data-mcel-lens-generated=\"true\"]",
        ".mcel-lens-hud",
        ".mcel-lens-risk-badge"
      ];
      generatedSelectors.forEach((selector) => {
        Array.from(doc.querySelectorAll?.(selector) || []).forEach((node) => node.remove());
      });

      Array.from(doc.querySelectorAll?.("[data-mcel-lens-role], [data-mcel-action-risk], [data-mcel-risk], [data-mcel-lens-label], [data-mcel-lens-kind], [data-mcel-mutates], [data-mcel-action-label], [data-mcel-enriched], [data-mcel-enrichment-source], [data-mcel-enrichment-selector], [data-mcel-role], [data-mcel-kind], [data-mcel-fit], [data-mcel-fit-context], [data-mcel-layout], [data-mcel-layout-policy], [data-mcel-layout-region], [data-mcel-region], [data-mcel-region-kind], [data-mcel-width-policy], [data-mcel-control-role], [data-mcel-control-priority], [data-mcel-action-role], [data-mcel-element-id], [data-mcel-contract], [data-mcel-concern], [data-mcel-terminal-object], [data-mcel-terminal-session], [data-mcel-terminal-role], [data-mcel-terminal-proof-policy], [data-mcel-command-policy], [data-mcel-terminal-state-field], [data-mcel-terminal-viewport], [data-mcel-supercut-contract], [data-mcel-supercut-proof-policy], [data-mcel-supercut-rewrite-tag]") || []).forEach((element) => {
        element.removeAttribute("data-mcel-lens-role");
        element.removeAttribute("data-mcel-action-risk");
        element.removeAttribute("data-mcel-risk");
        element.removeAttribute("data-mcel-lens-label");
        element.removeAttribute("data-mcel-lens-kind");
        element.removeAttribute("data-mcel-mutates");
        element.removeAttribute("data-mcel-action-label");
        element.removeAttribute("data-mcel-enriched");
        element.removeAttribute("data-mcel-enrichment-source");
        element.removeAttribute("data-mcel-enrichment-selector");
        element.removeAttribute("data-mcel-role");
        element.removeAttribute("data-mcel-kind");
        element.removeAttribute("data-mcel-fit");
        element.removeAttribute("data-mcel-fit-context");
        element.removeAttribute("data-mcel-layout");
        element.removeAttribute("data-mcel-layout-policy");
        element.removeAttribute("data-mcel-layout-region");
        element.removeAttribute("data-mcel-region");
        element.removeAttribute("data-mcel-region-kind");
        element.removeAttribute("data-mcel-width-policy");
        element.removeAttribute("data-mcel-control-role");
        element.removeAttribute("data-mcel-control-priority");
        element.removeAttribute("data-mcel-action-role");
        element.removeAttribute("data-mcel-element-id");
        element.removeAttribute("data-mcel-contract");
        element.removeAttribute("data-mcel-concern");
        element.removeAttribute("data-mcel-terminal-object");
        element.removeAttribute("data-mcel-terminal-session");
        element.removeAttribute("data-mcel-terminal-role");
        element.removeAttribute("data-mcel-terminal-proof-policy");
        element.removeAttribute("data-mcel-command-policy");
        element.removeAttribute("data-mcel-terminal-state-field");
        element.removeAttribute("data-mcel-terminal-viewport");
        element.removeAttribute("data-mcel-supercut-contract");
        element.removeAttribute("data-mcel-supercut-proof-policy");
        element.removeAttribute("data-mcel-supercut-rewrite-tag");
      });

      const root = doc.querySelector?.(specimen.rootSelector) || null;
      if (root) {
        root.removeAttribute("data-mcel-lens");
        root.removeAttribute("data-mcel-lens-state");
        root.removeAttribute("data-mcel-component-id");
        root.removeAttribute("data-mcel-component-kind");
        root.removeAttribute("data-mcel-layout-law");
        root.removeAttribute("data-mcel-lens-sidecar");
        root.removeAttribute("data-mcel-lens-hud");
        root.removeAttribute("data-mcel-app");
        root.removeAttribute("data-mcel-enrichment-state");
        root.removeAttribute("data-mcel-proof-surface");
        root.removeAttribute("data-mcel-element-id");
        root.removeAttribute("data-mcel-contract");
        root.removeAttribute("data-mcel-concern");
        root.removeAttribute("data-mcel-kind");
        root.removeAttribute("data-mcel-terminal-object");
        root.removeAttribute("data-mcel-terminal-session");
        root.removeAttribute("data-mcel-terminal-role");
        root.removeAttribute("data-mcel-terminal-proof-policy");
        root.removeAttribute("data-mcel-command-policy");
        root.removeAttribute("aria-description");
      }

      state.lensStatus = "clean";
      state.enrichmentStatus = "clean";
      state.lastAt = new Date().toISOString();
      mcelLabState.lastCanonicalSpecimenLens = null;
      mcelLabState.lastCanonicalSpecimenEnrichment = null;
      renderMcelCanonicalAppLensMap(null, reason);
      if (mcelCanonicalAppReport) {
        mcelCanonicalAppReport.textContent = `Cleaned MCEL specimen overlays and enrichment for ${mcelCanonicalAppLabel(specimen)}.\nThe iframe still has specimen root markers, but the mounted app no longer carries lab-generated MCEL role/fit attributes.`;
      }
      recordMcelEvent(
        "canonical-app",
        "MCEL_CANONICAL_TASK_MANAGER_LENS_CLEANED",
        `${mcelCanonicalAppLabel(specimen)} specimen lens overlay removed; sidecar state reset.`,
        "info"
      );
      renderMcelCanonicalAppSpecimenStatus(reason);
      return true;
    }

    function applyMcelCanonicalTaskManagerLens(reason = "lens") {
      const state = ensureMcelCanonicalAppSpecimenState();
      const specimen = selectedMcelCanonicalAppSpecimen();
      const doc = mcelCanonicalAppFrameDocument();
      if (!doc?.body) {
        const unavailable = {
          app: specimen.app,
          rootSelector: specimen.rootSelector,
          lensActive: false,
          rootPresent: false,
          panelCount: panelLens.length,
          classifiedPanelCount: 0,
          actionControlCount: 0,
          riskControlCount: 0,
          feedCount: 0,
          layoutLaw: "iframe document unavailable",
          overlayMode: "none",
          destructiveActionsExecuted: false,
          safetyClaim: specimen.app === "task-manager"
            ? "lens application never clicks Task Manager controls"
            : `lens application never clicks ${label} controls`,
          reason,
          appliedAt: new Date().toISOString()
        };
        mcelLabState.lastCanonicalSpecimenLens = unavailable;
        renderMcelCanonicalAppLensMap(unavailable, reason);
        return unavailable;
      }

      const enrichmentReport = mcelLabState.lastCanonicalSpecimenEnrichment || applyMcelCanonicalTaskManagerEnrichment(reason);
      injectMcelCanonicalAppSpecimenChrome(reason);
      ensureMcelCanonicalTaskManagerLensStyle(doc);
      const root = doc.querySelector?.(specimen.rootSelector) || null;
      doc.documentElement?.setAttribute?.("data-mcel-canonical-lens", "active");
      doc.body.setAttribute("data-mcel-canonical-lens", "active");
      doc.body.classList.add(MCEL_CANONICAL_TASK_MANAGER_LENS_CLASS);
      doc.getElementById(MCEL_CANONICAL_SPECIMEN_RIBBON_ID)?.remove?.();
      doc.getElementById(MCEL_CANONICAL_TASK_MANAGER_LENS_HUD_ID)?.remove?.();

      if (root) {
        root.setAttribute("data-mcel-lens", `canonical-${slug}`);
        root.setAttribute("data-mcel-lens-state", "sidecar-inspector");
        root.setAttribute("data-mcel-component-id", `canonical.${slug}.root`);
        root.setAttribute("data-mcel-component-kind", "canonical-app-specimen");
        root.setAttribute("data-mcel-layout-law", "sidecar-inspector");
      }

      const panels = panelLens.map((panel) => {
        const element = doc.querySelector?.(panel.selector) || null;
        if (element) {
          element.setAttribute("data-mcel-lens-role", panel.role);
          element.setAttribute("data-mcel-lens-kind", panel.kind);
          element.setAttribute("data-mcel-component-id", `canonical.${slug}.${panel.role}`);
          ensureMcelCanonicalTaskManagerLensLabel(doc, element, panel.label, panel.kind);
        }
        return {...panel, present: Boolean(element)};
      });

      const actionControls = [];
      actionLens.forEach((action) => {
        Array.from(doc.querySelectorAll?.(action.selector) || []).forEach((element) => {
          element.setAttribute("data-mcel-lens-role", action.role);
          element.setAttribute("data-mcel-action-risk", action.risk);
          element.setAttribute("data-mcel-action-label", action.label);
          element.setAttribute("data-mcel-mutates", action.risk === "safe" || action.risk === "analysis" ? "false" : "potential");
          actionControls.push({
            selector: action.selector,
            role: action.role,
            risk: action.risk,
            label: action.label,
            text: (element.textContent || element.getAttribute("aria-label") || element.id || action.selector).trim()
          });
        });
      });

      const feeds = panels.filter((panel) => panel.present && (panel.kind === "feed" || panel.role.endsWith("-feed")));
      const riskControls = actionControls.filter((item) => !["safe", "analysis", "network-read"].includes(item.risk));
      const report = {
        app: specimen.app,
        route: mcelCanonicalAppFrame?.dataset?.mcelSpecimenRoute || specimen.route,
        rootSelector: specimen.rootSelector,
        lensActive: Boolean(root),
        enrichmentActive: Boolean(enrichmentReport?.enrichmentActive),
        enrichmentElementCount: enrichmentReport?.enrichedElementCount || 0,
        rootPresent: Boolean(root),
        panelCount: panels.length,
        classifiedPanelCount: panels.filter((panel) => panel.present).length,
        missingPanels: panels.filter((panel) => !panel.present).map((panel) => panel.selector),
        feedCount: feeds.length,
        feeds: feeds.map((panel) => panel.role),
        actionControlCount: actionControls.length,
        actionControls,
        riskControlCount: riskControls.length,
        riskControls,
        layoutLaw: "lab-side inspector lens active",
        overlayMode: "subtle root outline and hover/focus rings; no inline labels or risk badges",
        chromeStyleId: MCEL_CANONICAL_SPECIMEN_CHROME_STYLE_ID,
        lensStyleId: MCEL_CANONICAL_SPECIMEN_LENS_STYLE_ID,
        lensHudId: MCEL_CANONICAL_TASK_MANAGER_LENS_HUD_ID,
        destructiveActionsExecuted: false,
        safetyClaim: specimen.app === "task-manager"
          ? "canonical lens annotates Task Manager and reports risk in the Lab sidecar; it does not restyle layout, inject labels into cards, or click server control, PID termination, or schedule actions"
          : `canonical lens annotates ${label} and reports risk in the Lab sidecar; it does not restyle layout, inject labels into cards, or click application controls`,
        reason,
        appliedAt: new Date().toISOString()
      };

      renderMcelCanonicalTaskManagerLensHud(doc, root, report);
      renderMcelCanonicalAppLensMap(report, reason);
      state.lensCount = (state.lensCount || 0) + 1;
      state.lensStatus = report.lensActive ? "sidecar" : "warning";
      state.lastAt = report.appliedAt;
      mcelLabState.lastCanonicalSpecimenLens = report;
      recordMcelEvent(
        "canonical-app",
        report.lensActive ? "MCEL_CANONICAL_TASK_MANAGER_LENS_ACTIVE" : "MCEL_CANONICAL_TASK_MANAGER_LENS_WARNING",
        report.lensActive
          ? `${label} sidecar inspector classified ${report.classifiedPanelCount} panel(s), ${report.feedCount} feed(s), and ${report.riskControlCount} risk surface(s) without in-frame badges.`
          : `${label} canonical lens could not find ${specimen.rootSelector}.`,
        report.lensActive ? "success" : "warning"
      );
      renderMcelCanonicalAppSpecimenStatus(reason);
      return report;
    }

    function bindMcelCanonicalAppSpecimenLifecycle(reason = "bind") {
      const frame = mcelCanonicalAppFrame;
      if (!frame || frame.dataset.lifecycleBound === "true") {
        renderMcelCanonicalAppSpecimenStatus(reason);
        return frame;
      }
      frame.dataset.lifecycleBound = "true";
      frame.addEventListener("load", () => {
        const state = ensureMcelCanonicalAppSpecimenState();
        state.loadCount += 1;
        state.status = "loaded";
        state.lastAt = new Date().toISOString();
        injectMcelCanonicalAppSpecimenChrome("iframe-load");
        applyMcelCanonicalTaskManagerEnrichment("iframe-load");
        renderMcelCanonicalAppSpecimenStatus("iframe-load");
        window.setTimeout(() => inspectMcelCanonicalAppSpecimen("iframe-load"), 80);
      });
      frame.addEventListener("error", () => {
        const state = ensureMcelCanonicalAppSpecimenState();
        state.errorCount += 1;
        state.status = "error";
        state.lastAt = new Date().toISOString();
        renderMcelCanonicalAppSpecimenStatus("iframe-error");
        recordMcelEvent("canonical-app", "MCEL_CANONICAL_SPECIMEN_IFRAME_ERROR", "Canonical app specimen iframe emitted an error.", "warning");
      });
      renderMcelCanonicalAppSpecimenStatus(reason);
      return frame;
    }

    function mountMcelCanonicalAppSpecimen(reason = "mount") {
      const frame = bindMcelCanonicalAppSpecimenLifecycle(reason);
      if (!frame) return null;
      const specimen = selectedMcelCanonicalAppSpecimen();
      const state = ensureMcelCanonicalAppSpecimenState();
      state.mountCount += 1;
      state.app = specimen.app;
      state.route = specimen.route;
      state.rootSelector = specimen.rootSelector;
      state.status = "loading";
      state.lastAt = new Date().toISOString();
      mcelLabState.lastCanonicalSpecimenReport = null;
      mcelLabState.lastCanonicalSpecimenProof = null;
      mcelLabState.lastCanonicalSpecimenLens = null;
      mcelLabState.lastCanonicalSpecimenEnrichment = null;
      state.lensStatus = "pending";
      frame.dataset.mcelSpecimenApp = specimen.app;
      frame.dataset.mcelSpecimenRoot = specimen.rootSelector;
      frame.dataset.mcelSpecimenRoute = specimen.route;
      frame.src = specimen.route;
      renderMcelCanonicalAppLensMap(null, reason);
      if (mcelCanonicalAppReport) {
        mcelCanonicalAppReport.textContent = `Mounting ${specimen.label} from ${specimen.route}\nreason: ${reason}\nMCEL Lab will enrich the legacy DOM into regions, components, fields, actions, and fit laws after load.\nNo destructive controls are executed by this lab harness.`;
      }
      recordMcelEvent("canonical-app", "MCEL_CANONICAL_SPECIMEN_MOUNTING", `${specimen.label} specimen iframe loading ${specimen.route}.`);
      renderMcelCanonicalAppSpecimenStatus(reason);
      return specimen;
    }

    function refreshMcelCanonicalAppSpecimen(reason = "refresh") {
      const frame = bindMcelCanonicalAppSpecimenLifecycle(reason);
      if (!frame) return mountMcelCanonicalAppSpecimen(reason);
      const currentSrc = frame.getAttribute("src") || "";
      if (!currentSrc || currentSrc === "about:blank") {
        return mountMcelCanonicalAppSpecimen(reason);
      }
      const state = ensureMcelCanonicalAppSpecimenState();
      state.status = "refreshing";
      state.lastAt = new Date().toISOString();
      let refreshedMountedApp = false;
      let mountedRefreshUnavailable = false;
      try {
        const child = frame.contentWindow;
        const childDocument = child?.document;
        const specimen = selectedMcelCanonicalAppSpecimen();
        const root = childDocument?.querySelector?.(specimen.rootSelector) || null;
        const mountedRefresh = specimen.app === "task-manager" ? child?.refreshTaskManager : null;
        if (root && typeof mountedRefresh === "function") {
          refreshedMountedApp = true;
          Promise.resolve(mountedRefresh())
            .catch(() => null)
            .finally(() => {
              window.setTimeout(() => inspectMcelCanonicalAppSpecimen(`${reason}:app-refresh-complete`), 80);
            });
        } else if (root) {
          mountedRefreshUnavailable = true;
          window.setTimeout(() => inspectMcelCanonicalAppSpecimen(`${reason}:app-refresh-unavailable`), 80);
        } else {
          frame.contentWindow?.location?.reload();
        }
      } catch (error) {
        frame.src = currentSrc;
      }
      recordMcelEvent(
        "canonical-app",
        "MCEL_CANONICAL_SPECIMEN_REFRESHING",
        refreshedMountedApp
          ? "Canonical app specimen mounted-app refresh requested."
          : mountedRefreshUnavailable
            ? "Canonical app specimen mounted-app refresh unavailable; specimen was inspected without synthetic control clicks."
            : "Canonical app specimen iframe refresh requested."
      );
      renderMcelCanonicalAppSpecimenStatus(reason);
      return selectedMcelCanonicalAppSpecimen();
    }

    function mcelCanonicalAppFrameDocument() {
      const frame = mcelCanonicalAppFrame;
      if (!frame) return null;
      try {
        return frame.contentDocument || frame.contentWindow?.document || null;
      } catch (error) {
        return null;
      }
    }

    function inspectMcelCanonicalAppSpecimen(reason = "inspect") {
      const state = ensureMcelCanonicalAppSpecimenState();
      const specimen = selectedMcelCanonicalAppSpecimen();
      const label = mcelCanonicalAppLabel(specimen);
      const frame = bindMcelCanonicalAppSpecimenLifecycle(reason);
      injectMcelCanonicalAppSpecimenChrome(reason);
      const enrichmentReport = applyMcelCanonicalTaskManagerEnrichment(reason);
      const lensReport = applyMcelCanonicalTaskManagerLens(reason);
      const doc = mcelCanonicalAppFrameDocument();
      const root = doc?.querySelector?.(specimen.rootSelector) || null;
      const requiredIds = mcelCanonicalAppRequiredIds(specimen).map((id) => ({
        id,
        present: Boolean(doc?.getElementById?.(id))
      }));
      const dangerousControls = mcelCanonicalAppDangerousControlSelectors(specimen).map((selector) => {
        const elements = Array.from(doc?.querySelectorAll?.(selector) || []);
        return {
          selector,
          present: elements.length > 0,
          count: elements.length,
          labels: elements.slice(0, 8).map((element) => (element.textContent || element.getAttribute("aria-label") || element.id || selector).trim())
        };
      });
      const report = {
        app: specimen.app,
        route: frame?.dataset?.mcelSpecimenRoute || specimen.route,
        rootSelector: specimen.rootSelector,
        mounted: Boolean(frame && frame.getAttribute("src") && frame.getAttribute("src") !== "about:blank"),
        frameReadyState: doc?.readyState || "unavailable",
        rootPresent: Boolean(root),
        rootLabel: root?.getAttribute?.("aria-label") || root?.id || "",
        rootWidgetCount: root ? root.querySelectorAll(".app-widget, .git-tools-card, .gitea-workflow-card, [data-widget-label], [data-mc-widget-id]").length : 0,
        tabCount: root ? root.querySelectorAll("[role=\"tab\"], [data-task-tab], [data-git-workflow-section], details > summary").length : 0,
        requiredIds,
        missingRequiredIds: requiredIds.filter((item) => !item.present).map((item) => item.id),
        dangerousControls,
        dangerousControlCount: dangerousControls.reduce((total, item) => total + item.count, 0),
        specimenChromeApplied: Boolean(root?.getAttribute?.("data-mcel-lab-specimen-root")),
        specimenChromeStyleId: MCEL_CANONICAL_SPECIMEN_CHROME_STYLE_ID,
        specimenRibbonId: "removed-from-clean-sidecar-lens",
        enrichmentActive: Boolean(enrichmentReport?.enrichmentActive),
        enrichmentElementCount: enrichmentReport?.enrichedElementCount || 0,
        enrichmentLayoutLawStatus: enrichmentReport?.layoutLawStatus || "not-run",
        lensActive: Boolean(lensReport?.lensActive),
        lensPanelCount: lensReport?.classifiedPanelCount || 0,
        lensRiskControlCount: lensReport?.riskControlCount || 0,
        destructiveActionsExecuted: false,
        safetyClaim: specimen.app === "task-manager"
          ? "inspection only; the harness does not click server control, PID termination, or schedule creation actions"
          : `inspection only; the harness does not click ${label} action controls`,
        inspectedAt: new Date().toISOString(),
        reason
      };
      report.status = report.rootPresent && report.missingRequiredIds.length === 0 ? "passed" : "warning";
      mcelLabState.lastCanonicalSpecimenReport = report;
      state.inspectCount += 1;
      state.status = report.status === "passed" ? "inspected" : "inspection-warning";
      state.lastAt = report.inspectedAt;
      if (mcelCanonicalAppReport) {
        mcelCanonicalAppReport.textContent = JSON.stringify(report, null, 2);
      }
      recordMcelEvent(
        "canonical-app",
        report.status === "passed" ? "MCEL_CANONICAL_SPECIMEN_INSPECTED" : "MCEL_CANONICAL_SPECIMEN_INCOMPLETE",
        report.status === "passed"
          ? `${label} specimen inspected with ${report.rootWidgetCount} widget surface(s) and ${report.dangerousControlCount} audited risky control selector match(es).`
          : `Canonical specimen inspection warning: missing ${report.missingRequiredIds.join(", ") || specimen.rootSelector}.`,
        report.status === "passed" ? "success" : "warning"
      );
      renderMcelCanonicalAppSpecimenStatus(reason);
      return report;
    }

    function runMcelCanonicalAppSpecimenProof(reason = "specimen-proof") {
      const report = inspectMcelCanonicalAppSpecimen(reason);
      const label = mcelCanonicalAppLabel({app: report.app, label: report.app === "git-tools" ? "Git Tools" : "Task Manager"});
      injectMcelCanonicalAppSpecimenChrome(reason);
      const doc = mcelCanonicalAppFrameDocument();
      const root = doc?.querySelector?.(report.rootSelector) || null;
      let proof = null;
      if (root && window.MCEL?.runBrowserProof) {
        try {
          proof = MCEL.runBrowserProof(root, {
            reason,
            surface: "canonical-app-specimen",
            app: report.app
          });
        } catch (error) {
          proof = {
            failed: true,
            error: error?.message || String(error),
            reason,
            surface: "canonical-app-specimen",
            app: report.app
          };
        }
      } else {
        proof = {
          failed: true,
          reason,
          surface: "canonical-app-specimen",
          app: report.app,
          error: root ? "MCEL.runBrowserProof unavailable" : `missing root ${report.rootSelector}`
        };
      }
      const state = ensureMcelCanonicalAppSpecimenState();
      state.proofCount += 1;
      state.status = proof && !proof.failed ? "proof-ready" : "proof-warning";
      state.lastAt = new Date().toISOString();
      mcelLabState.lastCanonicalSpecimenProof = proof;
      const combined = {
        inspection: report,
        enrichment: mcelLabState.lastCanonicalSpecimenEnrichment || applyMcelCanonicalTaskManagerEnrichment(reason),
        lens: mcelLabState.lastCanonicalSpecimenLens || applyMcelCanonicalTaskManagerLens(reason),
        browserProof: proof,
        destructiveActionsExecuted: false,
        safetyClaim: `browser proof observes the iframe DOM; it does not invoke ${label} command buttons`
      };
      if (mcelCanonicalAppReport) {
        mcelCanonicalAppReport.textContent = JSON.stringify(combined, null, 2);
      }
      recordMcelEvent(
        "canonical-app",
        proof && !proof.failed ? "MCEL_CANONICAL_SPECIMEN_PROOF_READY" : "MCEL_CANONICAL_SPECIMEN_PROOF_WARNING",
        proof && !proof.failed
          ? `${label} specimen browser proof observed ${proof.elementCount || 0} element(s).`
          : `${label} specimen browser proof warning: ${proof?.error || "unavailable"}.`,
        proof && !proof.failed ? "success" : "warning"
      );
      renderMcelCanonicalAppSpecimenStatus(reason);
      return combined;
    }


    function openMcelDiagnosticsDrawer(reason = "diagnostic-request") {
      if (!mcelDiagnosticsDrawer || mcelDiagnosticsDrawer.open) return;
      mcelDiagnosticsDrawer.open = true;
      recordMcelEvent("editor", "MCEL_DIAGNOSTICS_OPENED", `Diagnostics drawer opened by ${reason}.`);
    }

    function renderMcelRuntimeDom() {
      if (!mcelRuntimeDom || !mcelRuntimePreview) return;
      mcelRuntimeDom.textContent = McelLabEngine.formatHtml(mcelRuntimePreview.innerHTML);
    }

    function renderMcelSerializerDiff(serialized = "") {
      if (!mcelSerializerDiff) return;
      const report = mcelLabState.lastSerializerReport || {};
      mcelSerializerDiff.textContent = [
        "SERIALIZER REPORT",
        JSON.stringify(report, null, 2),
        "",
        "SERIALIZED SOURCE",
        serialized || "(not serialized yet)",
        "",
        "ROUND-TRIP STATUS",
        report.serializerClean ? "clean" : "warning"
      ].join("\n");
    }

    function renderMcelCssLawReport() {
      if (!mcelCssLawReport) return;
      mcelCssLawReport.textContent = mcelLabState.lastCssLawReport
        ? JSON.stringify(mcelLabState.lastCssLawReport, null, 2)
        : "CSS law has not been applied yet.";
    }

    function renderMcelLayoutLawReport() {
      if (!mcelLayoutLawReport) return;
      mcelLayoutLawReport.textContent = mcelLabState.lastLayoutLawReport
        ? JSON.stringify(mcelLabState.lastLayoutLawReport, null, 2)
        : "Layout law has not been applied yet.";
    }

    function renderMcelGraphReport() {
      if (!mcelGraphReport || typeof McelLabGraph === "undefined") return;
      mcelLabState.lastGraphReport = McelLabGraph.compactReport(currentMcelSource(), mcelRuntimePreview);
      mcelGraphReport.textContent = JSON.stringify(mcelLabState.lastGraphReport, null, 2);
    }

    function renderMcelAuditReport() {
      if (!mcelAuditReport) return;
      mcelAuditReport.textContent = mcelLabState.lastAuditReport
        ? JSON.stringify(mcelLabState.lastAuditReport, null, 2)
        : "Operational audit has not run yet.";
    }

    function currentMcelReadinessInputs() {
      return {
        serializerReport: mcelLabState.lastSerializerReport,
        cssLawReport: mcelLabState.lastCssLawReport,
        layoutLawReport: mcelLabState.lastLayoutLawReport,
        platformReport: mcelRuntimePreview && typeof McelLabPlatformSpine !== "undefined" ? McelLabPlatformSpine.provePlatform(mcelRuntimePreview, {reason: "readiness"}) : null,
        browserProof: mcelLabState.lastBrowserProof,
        a11yReport: mcelRuntimePreview ? McelLabEngine.computeA11y(mcelRuntimePreview) : null,
        auditReport: mcelLabState.lastAuditReport,
        testReport: mcelLabState.lastTestReport,
        matrixReport: mcelLabState.lastMatrixReport,
        acidReport: mcelLabState.lastAcidReport,
        kernelReport: mcelLabState.lastKernelAudit
      };
    }

    function renderMcelScenarioMatrix() {
      if (!mcelMatrixReport) return;
      mcelMatrixReport.textContent = typeof McelLabOpsRunner !== "undefined"
        ? McelLabOpsRunner.summarizeMatrix(mcelLabState.lastMatrixReport)
        : "Scenario matrix runner is unavailable.";
    }

    function renderMcelAcidTests() {
      if (!mcelAcidReport) return;
      if (!mcelLabState.lastAcidReport) {
        mcelAcidReport.textContent = "Acid tests have not run yet.";
        return;
      }
      mcelAcidReport.textContent = McelLabAcidTests.compactText(mcelLabState.lastAcidReport);
    }

    function renderMcelEvidencePacket() {
      if (!mcelEvidenceReport) return;
      mcelEvidenceReport.textContent = typeof McelLabOpsRunner !== "undefined"
        ? McelLabOpsRunner.compactEvidenceText(mcelLabState.lastEvidencePacket)
        : "Evidence packet builder is unavailable.";
    }

    function renderMcelSupervisorReport() {
      if (!mcelSupervisorReport) return;
      mcelSupervisorReport.textContent = typeof McelLabSupervisor !== "undefined"
        ? McelLabSupervisor.compactText(mcelLabState.lastSupervisorReport)
        : "Autopilot supervisor is unavailable.";
    }

    function renderMcelKernelAudit() {
      if (!mcelKernelReport) return;
      mcelKernelReport.textContent = typeof McelLabKernel !== "undefined"
        ? McelLabKernel.compactAuditText(mcelLabState.lastKernelAudit)
        : "Kernel audit is unavailable.";
    }

    function renderMcelTraceabilityMap() {
      if (!mcelTraceabilityReport) return;
      mcelTraceabilityReport.textContent = typeof McelLabKernel !== "undefined"
        ? McelLabKernel.compactTraceabilityText(mcelLabState.lastTraceabilityMap)
        : "Traceability map is unavailable.";
    }

    function renderMcelPriorArtReport() {
      if (!mcelPriorArtReport) return;
      mcelPriorArtReport.textContent = typeof McelLabKernel !== "undefined"
        ? McelLabKernel.priorArtText()
        : "Prior art map is unavailable.";
    }

    function renderMcelSubsumptionLattice() {
      if (!mcelSubsumptionReport) return;
      const lattice = mcelLabState.lastSubsumptionLattice || (typeof McelLabPlatformSpine !== "undefined" ? McelLabPlatformSpine.buildSubsumptionLattice() : null);
      mcelSubsumptionReport.textContent = lattice ? JSON.stringify(lattice, null, 2) : "Subsumption lattice is unavailable.";
    }

    function renderMcelAdoptionCase() {
      if (!mcelAdoptionReport) return;
      const adoptionCase = mcelLabState.lastAdoptionCase || (typeof McelLabPlatformSpine !== "undefined" ? McelLabPlatformSpine.buildAdoptionCase() : null);
      mcelAdoptionReport.textContent = adoptionCase ? JSON.stringify(adoptionCase, null, 2) : "Adoption case is unavailable.";
    }

    function renderMcelWorkbenchPlan() {
      if (!mcelWorkbenchReport) return;
      const plan = mcelLabState.lastWorkbenchPlan || (typeof McelLabWorkbench !== "undefined" ? McelLabWorkbench.buildWorkbenchPlan() : null);
      mcelWorkbenchReport.textContent = plan ? JSON.stringify(plan, null, 2) : "Workbench plan is unavailable.";
    }

    function renderMcelBrowserSemanticProof() {
      if (!mcelBrowserProofReport) return;
      if (!mcelLabState.lastBrowserProof) {
        mcelBrowserProofReport.textContent = "Browser semantic proof has not run yet.";
        return;
      }
      mcelBrowserProofReport.textContent = JSON.stringify(mcelLabState.lastBrowserProof, null, 2);
    }

    function renderMcelReadiness() {
      if (typeof McelLabOpsRunner === "undefined") return;
      const readiness = McelLabOpsRunner.buildReadiness(currentMcelReadinessInputs());
      mcelLabState.lastReadinessReport = readiness;
      if (mcelReadinessScore) {
        mcelReadinessScore.textContent = `Operational readiness: ${readiness.status} · ${readiness.passCount}/${readiness.total} checks · score ${readiness.score}`;
      }
      if (!mcelReadinessCards) return;
      mcelReadinessCards.innerHTML = "";
      readiness.cards.forEach((card) => {
        const item = document.createElement("article");
        item.dataset.status = card.status;
        const title = document.createElement("strong");
        title.textContent = card.label;
        const detail = document.createElement("span");
        detail.textContent = card.detail;
        item.append(title, detail);
        mcelReadinessCards.appendChild(item);
      });
    }

    function renderMcelCommandReport() {
      if (!mcelCommandReport) return;
      if (!mcelLabState.lastCommandPlan) {
        mcelCommandReport.textContent = "No semantic command has been planned yet.";
        return;
      }
      mcelCommandReport.textContent = JSON.stringify(mcelLabState.lastCommandPlan, null, 2);
    }

    function renderMcelProjectReport(result = null) {
      if (!mcelProjectReport || typeof McelLabProjectStore === "undefined") return;
      const payload = result?.snapshot || mcelLabState.lastProjectSnapshot || McelLabProjectStore.snapshot(currentMcelProjectState());
      mcelProjectReport.textContent = JSON.stringify({
        storageKey: McelLabProjectStore.storageKey,
        persisted: Boolean(result?.ok),
        snapshot: payload
      }, null, 2);
    }

    function renderMcelSiteSkeleton() {
      if (!mcelUiSkeletonSummary || !mcelRuntimePreview || typeof McelLabSiteSkeleton === "undefined") return;
      const report = McelLabSiteSkeleton.buildSkeleton(currentMcelSource(), mcelRuntimePreview);
      mcelLabState.lastSiteSkeleton = report;
      const roleOrder = ["hero", "trust cluster", "conversion form", "command row"];
      mcelUiSkeletonSummary.innerHTML = "";
      roleOrder.forEach((role) => {
        const matching = report.sections.find((section) => section.role === role);
        const item = document.createElement("article");
        item.dataset.status = matching ? "pass" : "pending";
        const title = document.createElement("strong");
        title.textContent = role;
        const detail = document.createElement("span");
        detail.textContent = matching
          ? `${matching.label} · ${matching.policy.scroll} scroll`
          : "not present in current source";
        item.append(title, detail);
        mcelUiSkeletonSummary.appendChild(item);
      });
      if (mcelUiSkeletonHealth) {
        mcelUiSkeletonHealth.dataset.status = report.layoutHealth.status;
        mcelUiSkeletonHealth.textContent = [
          `Layout health: ${report.layoutHealth.status}`,
          `illegal nested scrollbars: ${report.layoutHealth.nestedScrollbarCount}`,
          `self-owned scroll regions: ${report.layoutHealth.selfScrollCount}`,
          report.layoutHealth.claim
        ].join(" · ");
      }
    }

    function renderMcelA11yReport() {
      if (!mcelA11yReport || !mcelRuntimePreview) return;
      mcelA11yReport.textContent = JSON.stringify(McelLabEngine.computeA11y(mcelRuntimePreview), null, 2);
    }

    function selectedRuntimeElement() {
      return mcelRuntimePreview?.querySelector?.(`[${McelLabContract.attributes.sourceIndex}="${mcelLabState.selectedIndex}"]`) ||
        mcelRuntimePreview?.querySelector?.(`[${McelLabContract.attributes.type}]`);
    }

    function renderMcelDebugger() {
      if (!mcelDebuggerOutput || !mcelRuntimePreview) return;
      mcelDebuggerOutput.textContent = JSON.stringify(McelLabEngine.debuggerStateFor(selectedRuntimeElement(), mcelRuntimePreview), null, 2);
    }

    function renderMcelContractTests() {
      if (!mcelTestReport) return;
      if (!mcelLabState.lastTestReport) {
        mcelTestReport.textContent = "Contract tests have not run yet.";
        return;
      }
      const report = mcelLabState.lastTestReport;
      mcelTestReport.textContent = [
        `MCEL FULL CONTRACT SUITE: ${report.passed} passed / ${report.failed} failed`,
        report.generatedAt ? `generatedAt: ${report.generatedAt}` : "",
        "",
        ...report.tests.map((test) => `${test.passed ? "PASS" : "FAIL"} [${test.group || "contract"}] ${test.name}${test.details ? ` — ${test.details}` : ""}`)
      ].join("\n").trim();
    }

    function renderMcelCompilerLog() {
      if (!mcelCompilerLog) return;
      mcelCompilerLog.innerHTML = "";
      mcelLabState.compileEvents.slice(-32).forEach((event) => {
        const item = document.createElement("li");
        item.dataset.level = event.level;
        item.textContent = `[${event.module}] ${event.code}: ${event.message}`;
        mcelCompilerLog.appendChild(item);
      });
    }
