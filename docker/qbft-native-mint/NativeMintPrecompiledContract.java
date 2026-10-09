/*
 * Local Main Computer QBFT native-mint experiment.
 *
 * This file is copied into the Besu 26.9.0 source tree by patch_besu.py.
 * It is deliberately narrow: the genesis-seeded validator registry is the only
 * authority, approvals are ordinary transactions signed by validator keys, and
 * the third of four matching approvals performs the native balance increment.
 */
package org.hyperledger.besu.evm.precompile;

import org.hyperledger.besu.crypto.Hash;
import org.hyperledger.besu.datatypes.Address;
import org.hyperledger.besu.datatypes.Wei;
import org.hyperledger.besu.evm.account.MutableAccount;
import org.hyperledger.besu.evm.frame.MessageFrame;

import java.math.BigInteger;
import java.nio.charset.StandardCharsets;

import jakarta.validation.constraints.NotNull;
import org.apache.tuweni.bytes.Bytes;
import org.apache.tuweni.units.bigints.UInt256;

/** Consensus-visible local QBFT native issuance rule used only by the dev smoke lab. */
public final class NativeMintPrecompiledContract implements PrecompiledContract {
  public static final Address ADDRESS = Address.precompiled(0x01F0);

  private static final String NAME = "NATIVE_MINT_V1";
  private static final int INPUT_SIZE = 69;
  private static final byte VERSION = 0x01;
  private static final long GAS_REQUIREMENT = 120_000L;

  // Genesis-owned control slots on the precompile account.
  private static final UInt256 SLOT_VALIDATOR_COUNT = UInt256.valueOf(0);
  private static final UInt256 SLOT_REQUIRED_APPROVALS = UInt256.valueOf(1);
  private static final UInt256 SLOT_PROTOCOL_MAGIC = UInt256.valueOf(2);
  private static final int VALIDATOR_SLOT_BASE = 0x10;
  private static final UInt256 PROTOCOL_MAGIC =
      UInt256.fromBytes(Bytes.wrap(NAME.getBytes(StandardCharsets.US_ASCII)));

  private static final Bytes APPROVAL_COUNT_DOMAIN = Bytes.fromHexString("0x01");
  private static final Bytes APPROVAL_DOMAIN = Bytes.fromHexString("0x02");
  private static final Bytes CONSUMED_NONCE_DOMAIN = Bytes.fromHexString("0x03");

  @Override
  public String getName() {
    return NAME;
  }

  @Override
  public long gasRequirement(final Bytes input) {
    return GAS_REQUIREMENT;
  }

  @Override
  public @NotNull PrecompileContractResult computePrecompile(
      final Bytes input, @NotNull final MessageFrame frame) {
    try {
      if (frame.isStatic()
          || frame.getDepth() != 0
          || !frame.getSenderAddress().equals(frame.getOriginatorAddress())
          || input.size() != INPUT_SIZE
          || input.get(0) != VERSION) {
        return reject();
      }

      final MutableAccount control = frame.getWorldUpdater().getAccount(ADDRESS);
      if (control == null || !control.getStorageValue(SLOT_PROTOCOL_MAGIC).equals(PROTOCOL_MAGIC)) {
        // The modified client alone is not enough. Genesis must explicitly enable the rule.
        return reject();
      }

      final int validatorCount = control.getStorageValue(SLOT_VALIDATOR_COUNT).intValue();
      final int requiredApprovals = control.getStorageValue(SLOT_REQUIRED_APPROVALS).intValue();
      if (validatorCount < 1
          || requiredApprovals != ((validatorCount * 2) / 3) + 1
          || requiredApprovals > validatorCount) {
        return reject();
      }

      final Address sender = frame.getSenderAddress();
      if (!isValidator(control, sender, validatorCount)) {
        return reject();
      }

      final Bytes nonceBytes = input.slice(1, 8);
      final long expiryBlock = input.slice(9, 8).toLong();
      if (Long.compareUnsigned(frame.getBlockValues().getNumber(), expiryBlock) > 0) {
        return reject();
      }

      final Address recipient = Address.wrap(input.slice(17, Address.SIZE));
      final BigInteger amountInteger = input.slice(37, 32).toUnsignedBigInteger();
      if (amountInteger.signum() <= 0) {
        return reject();
      }
      final Wei amount = Wei.of(amountInteger);

      final UInt256 consumedNonceSlot = storageKey(CONSUMED_NONCE_DOMAIN, nonceBytes);
      if (!control.getStorageValue(consumedNonceSlot).isZero()) {
        return reject();
      }

      // The exact operation bytes bind nonce, expiry, recipient and amount together.
      final Bytes operationHash = Hash.keccak256(input);
      final UInt256 approvalSlot = storageKey(APPROVAL_DOMAIN, operationHash, sender.getBytes());
      if (!control.getStorageValue(approvalSlot).isZero()) {
        // A validator cannot count twice.
        return PrecompileContractResult.success(Bytes.of(0x01));
      }

      control.setStorageValue(approvalSlot, UInt256.valueOf(1));
      final UInt256 countSlot = storageKey(APPROVAL_COUNT_DOMAIN, operationHash);
      final int priorCount = control.getStorageValue(countSlot).intValue();
      final int newCount = priorCount + 1;
      control.setStorageValue(countSlot, UInt256.valueOf(newCount));

      if (newCount < requiredApprovals) {
        return PrecompileContractResult.success(Bytes.of(0x01));
      }

      final MutableAccount recipientAccount = frame.getWorldUpdater().getOrCreate(recipient);
      recipientAccount.incrementBalance(amount);
      control.setStorageValue(consumedNonceSlot, UInt256.valueOf(1));
      return PrecompileContractResult.success(Bytes.of(0x02));
    } catch (final RuntimeException ignored) {
      // Invalid/corrupt operation data must deterministically reject on every node.
      return reject();
    }
  }

  private static boolean isValidator(
      final MutableAccount control, final Address sender, final int validatorCount) {
    final UInt256 senderValue = UInt256.fromBytes(sender.getBytes());
    for (int index = 0; index < validatorCount; index++) {
      if (control
          .getStorageValue(UInt256.valueOf(VALIDATOR_SLOT_BASE + index))
          .equals(senderValue)) {
        return true;
      }
    }
    return false;
  }

  private static UInt256 storageKey(final Bytes domain, final Bytes... components) {
    final Bytes[] values = new Bytes[components.length + 1];
    values[0] = domain;
    System.arraycopy(components, 0, values, 1, components.length);
    return UInt256.fromBytes(Hash.keccak256(Bytes.concatenate(values)));
  }

  private static PrecompileContractResult reject() {
    return PrecompileContractResult.revert(Bytes.EMPTY);
  }
}
