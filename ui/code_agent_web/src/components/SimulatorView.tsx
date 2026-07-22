'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { CodeAgentApiClient } from '@/lib/api-client';
import { SimulatorDevice, SimulatorUiTree, WdaStatus } from '@/lib/models';
import { Smartphone, RefreshCw, MousePointerClick, Eye, EyeOff } from 'lucide-react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

const SCREENSHOT_POLL_MS = 1000;
const UI_TREE_POLL_MS = 2000;
const TAP_FEEDBACK_MS = 350;

export default function SimulatorView() {
  const [devices, setDevices] = useState<SimulatorDevice[]>([]);
  const [selectedUdid, setSelectedUdid] = useState<string | null>(null);
  const [isLoadingDevices, setIsLoadingDevices] = useState(false);
  const [devicesError, setDevicesError] = useState<string | null>(null);

  const [screenshotSrc, setScreenshotSrc] = useState<string | null>(null);
  const [screenshotError, setScreenshotError] = useState<string | null>(null);

  const [wdaStatus, setWdaStatus] = useState<WdaStatus>({ state: 'not_started' });
  const [showOverlay, setShowOverlay] = useState(false);
  const [uiTree, setUiTree] = useState<SimulatorUiTree | null>(null);

  const [tapPoint, setTapPoint] = useState<{ x: number; y: number } | null>(null);
  const tapFeedbackTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const imgRef = useRef<HTMLImageElement>(null);
  const selectedDevice = devices.find((d) => d.udid === selectedUdid) || null;

  const loadDevices = useCallback(async () => {
    setIsLoadingDevices(true);
    setDevicesError(null);
    try {
      const list = await CodeAgentApiClient.listSimulatorDevices();
      setDevices(list);
      if (!selectedUdid && list.length > 0) {
        setSelectedUdid(list[0].udid);
      }
    } catch (err: any) {
      setDevicesError(err.message || 'Could not reach the backend simulator API');
    } finally {
      setIsLoadingDevices(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadDevices();
  }, [loadDevices]);

  // Reset per-device state when the selection changes, then sync WDA status
  // from the backend — it may already be building/ready from a prior session
  // (e.g. after a page reload), and defaulting to 'not_started' would hide that.
  useEffect(() => {
    setScreenshotSrc(null);
    setScreenshotError(null);
    setWdaStatus({ state: 'not_started' });
    setUiTree(null);
    setShowOverlay(false);
    if (!selectedUdid) return;
    let cancelled = false;
    CodeAgentApiClient.getSimulatorWdaStatus(selectedUdid)
      .then((status) => {
        if (!cancelled) setWdaStatus(status);
      })
      .catch(() => {
        // Backend unreachable — leave the default 'not_started' state.
      });
    return () => {
      cancelled = true;
    };
  }, [selectedUdid]);

  // Poll the live screenshot for the selected, booted device.
  useEffect(() => {
    if (!selectedUdid || !selectedDevice || selectedDevice.state !== 'Booted') return;
    let cancelled = false;

    const tick = () => {
      if (cancelled) return;
      setScreenshotSrc(CodeAgentApiClient.getSimulatorScreenshotUrl(selectedUdid));
    };
    tick();
    const interval = setInterval(tick, SCREENSHOT_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [selectedUdid, selectedDevice]);

  // Poll the UI-element overlay while enabled and WDA is ready.
  useEffect(() => {
    if (!selectedUdid || !showOverlay || wdaStatus.state !== 'ready') return;
    let cancelled = false;

    const tick = async () => {
      try {
        const tree = await CodeAgentApiClient.getSimulatorUiTree(selectedUdid);
        if (!cancelled) setUiTree(tree);
      } catch {
        // Transient failures are fine — keep the last-known overlay.
      }
    };
    tick();
    const interval = setInterval(tick, UI_TREE_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [selectedUdid, showOverlay, wdaStatus.state]);

  // Poll WDA build/bootstrap status while it's in flight.
  useEffect(() => {
    if (!selectedUdid || wdaStatus.state !== 'building') return;
    let cancelled = false;
    const interval = setInterval(async () => {
      try {
        const status = await CodeAgentApiClient.getSimulatorWdaStatus(selectedUdid);
        if (!cancelled) setWdaStatus(status);
      } catch {
        // keep polling — a transient fetch error shouldn't abandon the wait
      }
    }, 2000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [selectedUdid, wdaStatus.state]);

  const handleBootAndSelect = async (udid: string) => {
    setSelectedUdid(udid);
    const device = devices.find((d) => d.udid === udid);
    if (device && device.state !== 'Booted') {
      try {
        await CodeAgentApiClient.bootSimulatorDevice(udid);
        await loadDevices();
      } catch (err: any) {
        toast.error(err.message || 'Failed to boot simulator');
      }
    }
  };

  const handleEnableInteraction = async () => {
    if (!selectedUdid) return;
    try {
      const status = await CodeAgentApiClient.startSimulatorWda(selectedUdid);
      setWdaStatus(status);
    } catch (err: any) {
      toast.error(err.message || 'Failed to start WebDriverAgent');
    }
  };

  const handleTap = async (e: React.MouseEvent<HTMLDivElement>) => {
    if (!selectedUdid || wdaStatus.state !== 'ready') return;
    // Lazily fetch the hierarchy on first tap if the overlay hasn't loaded it yet —
    // we need root_width/root_height to convert pixel clicks into device points.
    let tree = uiTree;
    if (!tree) {
      try {
        tree = await CodeAgentApiClient.getSimulatorUiTree(selectedUdid);
        setUiTree(tree);
      } catch (err: any) {
        toast.error(err.message || 'Could not read device UI tree for tap mapping');
        return;
      }
    }
    const img = imgRef.current;
    if (!img || !tree || tree.root_width <= 0 || tree.root_height <= 0) return;
    const rect = img.getBoundingClientRect();
    const localX = e.clientX - rect.left;
    const localY = e.clientY - rect.top;
    const x = (localX / rect.width) * tree.root_width;
    const y = (localY / rect.height) * tree.root_height;

    if (tapFeedbackTimer.current) clearTimeout(tapFeedbackTimer.current);
    setTapPoint({ x: localX, y: localY });
    tapFeedbackTimer.current = setTimeout(() => setTapPoint(null), TAP_FEEDBACK_MS);

    try {
      await CodeAgentApiClient.tapSimulator(selectedUdid, x, y);
    } catch (err: any) {
      toast.error(err.message || 'Tap failed');
    }
  };

  return (
    <div className="flex-1 bg-white p-6 md:p-8 overflow-y-auto h-full space-y-6 select-none">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-slate-800 flex items-center gap-2">
            <Smartphone size={22} className="text-slate-600" />
            <span>Simulator</span>
          </h2>
          <p className="text-xs text-slate-400 font-medium">
            Live iOS Simulator mirror — independent of any code-agent run.
          </p>
        </div>
        <button
          onClick={loadDevices}
          disabled={isLoadingDevices}
          className="flex items-center gap-1.5 text-xs font-semibold text-slate-500 hover:text-slate-800 disabled:opacity-50 px-2.5 py-1.5 rounded-lg hover:bg-slate-100 transition-colors"
        >
          <RefreshCw size={13} className={isLoadingDevices ? 'animate-spin' : ''} />
          Refresh devices
        </button>
      </div>

      {devicesError ? (
        <div className="p-4 border border-dashed border-rose-200 bg-rose-50 rounded-xl text-xs text-rose-700 font-medium">
          {devicesError}
        </div>
      ) : null}

      <div className="flex items-center gap-3">
        <div className="w-72">
          <Select
            value={selectedUdid || undefined}
            onValueChange={(udid) => handleBootAndSelect(udid)}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select a simulator" />
            </SelectTrigger>
            <SelectContent>
              {devices.map((d) => (
                <SelectItem key={d.udid} value={d.udid}>
                  {d.name} — {d.runtime} ({d.state})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {selectedDevice && selectedDevice.state === 'Booted' ? (
          <>
            <button
              onClick={handleEnableInteraction}
              disabled={wdaStatus.state === 'building' || wdaStatus.state === 'ready'}
              className="flex items-center gap-1.5 text-xs font-semibold text-white bg-[#ec3013] hover:bg-[#dd2b0f] disabled:opacity-60 px-3 py-2 rounded-lg transition-colors"
            >
              <MousePointerClick size={13} />
              {wdaStatus.state === 'ready'
                ? 'Interaction enabled'
                : wdaStatus.state === 'building'
                ? 'Building…'
                : 'Enable interaction'}
            </button>

            <button
              onClick={() => setShowOverlay((v) => !v)}
              disabled={wdaStatus.state !== 'ready'}
              className="flex items-center gap-1.5 text-xs font-semibold text-slate-600 hover:text-slate-900 disabled:opacity-40 px-2.5 py-2 rounded-lg hover:bg-slate-100 transition-colors"
            >
              {showOverlay ? <EyeOff size={13} /> : <Eye size={13} />}
              UI bounds
            </button>
          </>
        ) : null}
      </div>

      {wdaStatus.state === 'building' ? (
        <div className="p-3 border border-amber-200 bg-amber-50 rounded-xl text-xs text-amber-800 font-medium">
          Building WebDriverAgent… first run can take a couple of minutes (clones and
          builds the WebDriverAgent Xcode project). Later runs are fast.
        </div>
      ) : null}
      {wdaStatus.state === 'failed' ? (
        <div className="p-3 border border-rose-200 bg-rose-50 rounded-xl text-xs text-rose-700 font-medium">
          {wdaStatus.error || 'WebDriverAgent failed to start.'}
        </div>
      ) : null}

      {!selectedDevice ? (
        <div className="flex flex-col items-center justify-center p-12 border border-dashed border-slate-200 rounded-xl h-64 text-center">
          <Smartphone size={32} className="text-slate-300 mb-2" />
          <p className="text-xs text-slate-500 font-medium leading-normal max-w-[280px]">
            {devices.length === 0
              ? 'No simulators found. Make sure Xcode command line tools are installed.'
              : 'Select a simulator above to see its live screen.'}
          </p>
        </div>
      ) : selectedDevice.state !== 'Booted' ? (
        <div className="flex flex-col items-center justify-center p-12 border border-dashed border-slate-200 rounded-xl h-64 text-center">
          <Smartphone size={32} className="text-slate-300 mb-2" />
          <p className="text-xs text-slate-500 font-medium leading-normal max-w-[280px]">
            Booting {selectedDevice.name}…
          </p>
        </div>
      ) : (
        <div className="flex justify-center">
          <div
            className="relative inline-block border border-slate-200 rounded-2xl overflow-hidden bg-black cursor-crosshair"
            onClick={handleTap}
          >
            {screenshotSrc ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                ref={imgRef}
                src={screenshotSrc}
                alt={`${selectedDevice.name} live mirror`}
                className="max-h-[70vh] block select-none"
                draggable={false}
                onError={() => setScreenshotError('Screenshot capture failed')}
              />
            ) : (
              <div className="w-[280px] h-[600px] flex items-center justify-center text-xs text-white/50 font-medium">
                Loading preview…
              </div>
            )}

            {showOverlay && uiTree && uiTree.root_width > 0 ? (
              <div className="absolute inset-0 pointer-events-none">
                {uiTree.elements.map((el) => (
                  <div
                    key={el.index}
                    className="absolute border border-cyan-400/80"
                    style={{
                      left: `${(el.x / uiTree.root_width) * 100}%`,
                      top: `${(el.y / uiTree.root_height) * 100}%`,
                      width: `${(el.width / uiTree.root_width) * 100}%`,
                      height: `${(el.height / uiTree.root_height) * 100}%`,
                    }}
                  />
                ))}
              </div>
            ) : null}

            {tapPoint ? (
              <div
                className="absolute rounded-full border-2 border-orange-400 bg-orange-400/25 pointer-events-none"
                style={{
                  left: tapPoint.x - 18,
                  top: tapPoint.y - 18,
                  width: 36,
                  height: 36,
                }}
              />
            ) : null}
          </div>
        </div>
      )}

      {screenshotError ? (
        <p className="text-center text-xs text-rose-500 font-medium">{screenshotError}</p>
      ) : null}
    </div>
  );
}
