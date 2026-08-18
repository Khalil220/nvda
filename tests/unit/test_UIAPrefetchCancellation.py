# A part of NonVisual Desktop Access (NVDA)
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.
# Copyright (C) 2026 NV Access Limited

"""Unit tests for cancellation of UIA property prefetching.

``UIA._prefetchUIACacheForPropertyIDs`` issues ``IUIAutomationElement.buildUpdatedCache``,
a blocking cross-process call. When the provider is slow to answer it blocks the core
thread for UIA's own internal timeout, and ``CoCancelCall`` cannot cancel it, so the call
is routed through the watchdog's cancellable thread instead (#20654).
"""

import contextlib
import unittest
from unittest.mock import Mock, patch

from comtypes import COMError

import exceptions
from NVDAObjects.UIA import UIA


class _PrefetchTestCase(unittest.TestCase):
	"""Builds a bare UIA object with a controllable UIAElement."""

	#: Two arbitrary UIA property IDs. Two is the minimum; the method returns early
	#: for fewer, since a cache request for one property is pointless.
	PROPERTY_IDS = frozenset({30008, 30009})

	def makeObj(self) -> UIA:
		obj = object.__new__(UIA)
		obj.UIAElement = Mock()
		# Start with an empty per-core-cycle cache so nothing is skipped.
		obj._coreCycleUIAPropertyCacheElementCache = {}
		return obj

	@contextlib.contextmanager
	def uiaHandler(self):
		"""Stand in for the UIA handler, which is not initialized in unit tests."""
		handler = Mock()
		handler.clientObject.createCacheRequest.return_value = Mock()
		with patch("NVDAObjects.UIA.UIAHandler.handler", handler):
			yield handler


class TestPrefetchRoutedThroughWatchdog(_PrefetchTestCase):
	def test_buildUpdatedCacheIsRunViaCancellableExecute(self):
		"""The blocking call must not be made directly on the calling thread."""
		obj = self.makeObj()
		cacheElement = Mock()
		with (
			self.uiaHandler(),
			patch(
				"NVDAObjects.UIA.watchdog.cancellableExecute",
				return_value=cacheElement,
			) as cancellableExecute,
		):
			obj._prefetchUIACacheForPropertyIDs(set(self.PROPERTY_IDS))
		cancellableExecute.assert_called_once()
		# The callable handed to the watchdog must be buildUpdatedCache itself.
		self.assertIs(cancellableExecute.call_args.args[0], obj.UIAElement.buildUpdatedCache)
		# And the results must still be cached for the rest of the core cycle.
		for propertyId in self.PROPERTY_IDS:
			self.assertIs(obj._coreCycleUIAPropertyCacheElementCache[propertyId], cacheElement)

	def test_cancellationIsHandledLikeFailure(self):
		"""On cancellation we return quietly, caching nothing, as for a COMError."""
		obj = self.makeObj()
		with (
			self.uiaHandler(),
			patch(
				"NVDAObjects.UIA.watchdog.cancellableExecute",
				side_effect=exceptions.CallCancelled,
			),
		):
			obj._prefetchUIACacheForPropertyIDs(set(self.PROPERTY_IDS))
		self.assertEqual(obj._coreCycleUIAPropertyCacheElementCache, {})

	def test_comErrorStillHandled(self):
		"""The pre-existing COMError path must be preserved."""
		obj = self.makeObj()
		with (
			self.uiaHandler(),
			patch(
				"NVDAObjects.UIA.watchdog.cancellableExecute",
				side_effect=COMError(-2147467259, "Unspecified error", None),
			),
		):
			obj._prefetchUIACacheForPropertyIDs(set(self.PROPERTY_IDS))
		self.assertEqual(obj._coreCycleUIAPropertyCacheElementCache, {})

	def test_cancellationDoesNotPropagate(self):
		"""A cancelled prefetch must not surface as an exception to callers.

		Property fetches happen all over the object model; letting CallCancelled escape
		would turn a recoverable freeze into an error for every caller.
		"""
		obj = self.makeObj()
		with (
			self.uiaHandler(),
			patch(
				"NVDAObjects.UIA.watchdog.cancellableExecute",
				side_effect=exceptions.CallCancelled,
			),
		):
			try:
				obj._prefetchUIACacheForPropertyIDs(set(self.PROPERTY_IDS))
			except exceptions.CallCancelled:
				self.fail("CallCancelled escaped _prefetchUIACacheForPropertyIDs")


class TestPrefetchEarlyReturns(_PrefetchTestCase):
	"""Cases which must not reach the cross-process call at all."""

	def test_noCallForFewerThanTwoProperties(self):
		obj = self.makeObj()
		with patch("NVDAObjects.UIA.watchdog.cancellableExecute") as cancellableExecute:
			obj._prefetchUIACacheForPropertyIDs({30008})
		cancellableExecute.assert_not_called()

	def test_noCallWhenAlreadyCached(self):
		obj = self.makeObj()
		obj._coreCycleUIAPropertyCacheElementCache = {pid: Mock() for pid in self.PROPERTY_IDS}
		with patch("NVDAObjects.UIA.watchdog.cancellableExecute") as cancellableExecute:
			obj._prefetchUIACacheForPropertyIDs(set(self.PROPERTY_IDS))
		cancellableExecute.assert_not_called()


if __name__ == "__main__":
	unittest.main()
