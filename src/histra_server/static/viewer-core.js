import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const DEFAULT_OPTIONS = {
  background: 0x101318,
  edges: true,
  nodes: false,
  modelPoints: true,
  extrude: true,
  thicknessScale: 1,
  opacity: 0.94,
  colorBy: 'material',
};

function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function mean(values) {
  return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
}

function parseVector(value, fallback = [0, 0, 0]) {
  if (Array.isArray(value)) {
    const parts = value.map(Number);
    return parts.length === 3 && parts.every(Number.isFinite)
      ? new THREE.Vector3(parts[0], parts[1], parts[2])
      : new THREE.Vector3(...fallback);
  }
  if (!value) return new THREE.Vector3(...fallback);
  const parts = String(value).split(';').map(Number);
  return parts.length === 3 && parts.every(Number.isFinite)
    ? new THREE.Vector3(parts[0], parts[1], parts[2])
    : new THREE.Vector3(...fallback);
}

function computedNormal(points) {
  const a = points[1].clone().sub(points[0]);
  const b = points[3].clone().sub(points[0]);
  const normal = a.cross(b);
  if (normal.lengthSq() <= 1e-18) {
    normal.copy(points[2]).sub(points[0]).cross(points[3].clone().sub(points[1]));
  }
  return normal.lengthSq() > 1e-18 ? normal.normalize() : new THREE.Vector3(0, -1, 0);
}

function hashString(value) {
  let hash = 2166136261;
  for (const character of String(value)) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function normalizePreviewMesh(mesh, name = 'Server preview') {
  const nodes = new Map();
  for (const node of mesh?.nodes ?? []) {
    const key = Number(node.key);
    if (Number.isInteger(key)) nodes.set(key, parseVector(node.point));
  }
  const quads = [];
  for (const item of mesh?.quads ?? []) {
    const nodeKeys = (item.nodeKeys ?? []).map(Number);
    if (nodeKeys.length !== 4 || nodeKeys.some(key => !nodes.has(key))) continue;
    const points = nodeKeys.map(key => nodes.get(key));
    const normal = computedNormal(points);
    quads.push({
      key: finiteNumber(item.key),
      name: String(item.name ?? ''),
      nodeKeys,
      material: String(item.materialKey ?? '0'),
      layer: finiteNumber(item.layerKey, 0),
      parentType: String(item.parentTypeElement ?? 'Bridge'),
      parentKey: finiteNumber(item.parentKey, 1),
      thicknesses: [0, 1, 2, 3].map(() => finiteNumber(item.thickness, 0)),
      normal,
      group: String(item.group ?? 'mesh'),
      lane: String(item.transverseBandName ?? item.transverseRole ?? '—'),
      transverseRole: String(item.transverseRole ?? '—'),
      sourceIndex: item.sourceIndex,
    });
  }
  return { name, nodes, quads };
}

export async function decodeLocalFile(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe) {
    return new TextDecoder('utf-16le').decode(bytes.subarray(2));
  }
  if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) {
    const swapped = new Uint8Array(bytes.length - 2);
    for (let index = 2; index + 1 < bytes.length; index += 2) {
      swapped[index - 2] = bytes[index + 1];
      swapped[index - 1] = bytes[index];
    }
    return new TextDecoder('utf-16le').decode(swapped);
  }
  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    return new TextDecoder('utf-8').decode(bytes.subarray(3));
  }
  const sampleLength = Math.min(bytes.length, 4096);
  let nulCount = 0;
  for (let index = 0; index < sampleLength; index += 1) if (bytes[index] === 0) nulCount += 1;
  return new TextDecoder(sampleLength > 0 && nulCount / sampleLength > 0.10 ? 'utf-16le' : 'utf-8').decode(bytes);
}

export function parseHrxXml(xmlText, name = 'HRX model') {
  const cleanText = String(xmlText).replace(/^\s*<\?xml[^?]*\?>/i, '');
  const xml = new DOMParser().parseFromString(cleanText, 'application/xml');
  const parseError = xml.querySelector('parsererror');
  if (parseError) throw new Error(`XML parse error: ${parseError.textContent.trim()}`);

  const nodes = new Map();
  for (const element of xml.getElementsByTagName('Node')) {
    const key = Number(element.getAttribute('Key'));
    const point = element.getAttribute('Point');
    if (Number.isInteger(key) && point) nodes.set(key, parseVector(point));
  }

  const quads = [];
  for (const element of xml.getElementsByTagName('Quad')) {
    const nodeKeys = [1, 2, 3, 4].map(index => Number(element.getAttribute(`NodeKey${index}`)));
    if (nodeKeys.some(key => !Number.isInteger(key))) continue;
    const missing = nodeKeys.filter(key => !nodes.has(key));
    if (missing.length) {
      throw new Error(`Quad ${element.getAttribute('Key') ?? '?'} references missing nodes: ${missing.join(', ')}`);
    }
    const normals = [1, 2, 3, 4].map(index => parseVector(element.getAttribute(`Normal${index}`)));
    const normal = normals.reduce((sum, item) => sum.add(item), new THREE.Vector3());
    if (normal.lengthSq() > 1e-18) normal.normalize();
    else normal.copy(computedNormal(nodeKeys.map(key => nodes.get(key))));
    quads.push({
      key: finiteNumber(element.getAttribute('Key')),
      name: element.getAttribute('Name') ?? '',
      nodeKeys,
      material: String(element.getAttribute('MaterialKey') ?? '0'),
      layer: finiteNumber(element.getAttribute('LayerKey')),
      parentType: element.getAttribute('ParentTypeElement') ?? '',
      parentKey: finiteNumber(element.getAttribute('ParentKey')),
      thicknesses: [1, 2, 3, 4].map(index => finiteNumber(element.getAttribute(`Thickness${index}`))),
      normal,
      group: element.getAttribute('ParentTypeElement') ?? 'Quad',
      lane: `Y ${mean(nodeKeys.map(key => nodes.get(key).y)).toFixed(3)}`,
      transverseRole: '',
      sourceIndex: null,
    });
  }
  if (!nodes.size) throw new Error('No valid <Node> elements were found.');
  if (!quads.length) throw new Error('No valid <Quad> elements were found.');
  return { name, nodes, quads };
}

export class QuadViewer {
  constructor(container, options = {}) {
    if (!container) throw new Error('QuadViewer requires a container element.');
    this.container = container;
    this.options = { ...DEFAULT_OPTIONS, ...options };
    this.callbacks = { selection: null, stats: null, legend: null, message: null };
    this.state = {
      name: '',
      nodes: new Map(),
      quads: [],
      modelPoints: [],
      mesh: null,
      edgeLines: null,
      nodePoints: null,
      modelPointPoints: null,
      selectionLine: null,
      triangleToQuad: [],
      selectedQuadIndex: null,
      bounds: new THREE.Box3(),
      categoryColors: new Map(),
    };

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(this.options.background);
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.01, 1e9);
    this.camera.up.set(0, 0, 1);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.domElement.style.position = 'absolute';
    this.renderer.domElement.style.inset = '0';
    this.container.prepend(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.screenSpacePanning = true;

    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x283343, 2.1));
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.5);
    keyLight.position.set(-1, -2, 3);
    this.scene.add(keyLight);

    this.axes = new THREE.AxesHelper(35);
    this.axes.renderOrder = 5;
    this.scene.add(this.axes);

    this.grid = new THREE.GridHelper(1200, 24, 0x536175, 0x293241);
    this.grid.rotation.x = Math.PI / 2;
    this.grid.material.transparent = true;
    this.grid.material.opacity = 0.34;
    this.grid.material.depthWrite = false;
    this.scene.add(this.grid);

    this.modelGroup = new THREE.Group();
    this.scene.add(this.modelGroup);
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this._pointerStart = null;
    this._resizeObserver = new ResizeObserver(() => this.resize());
    this._resizeObserver.observe(this.container);
    this.renderer.domElement.addEventListener('pointerdown', event => this._onPointerDown(event));
    this.renderer.domElement.addEventListener('pointerup', event => this._onPointerUp(event));
    this.resize();
    this._animate();
  }

  on(event, callback) {
    if (!(event in this.callbacks)) throw new Error(`Unknown viewer event: ${event}`);
    this.callbacks[event] = callback;
    return this;
  }

  emit(event, value) {
    this.callbacks[event]?.(value);
  }

  loadPreview(mesh, { name = 'Server preview', modelPoints = [] } = {}) {
    const normalized = normalizePreviewMesh(mesh, name);
    this._loadNormalized(normalized, modelPoints);
  }

  loadHrxText(xmlText, name = 'HRX model') {
    const normalized = parseHrxXml(xmlText, name);
    this._loadNormalized(normalized, []);
  }

  clear() {
    this._disposeObject(this.state.mesh);
    this._disposeObject(this.state.edgeLines);
    this._disposeObject(this.state.nodePoints);
    this._disposeObject(this.state.modelPointPoints);
    this._disposeObject(this.state.selectionLine);
    this.state = { ...this.state, name: '', nodes: new Map(), quads: [], modelPoints: [], mesh: null, edgeLines: null, nodePoints: null, modelPointPoints: null, selectionLine: null, triangleToQuad: [], selectedQuadIndex: null, bounds: new THREE.Box3(), categoryColors: new Map() };
    this.emit('stats', this.getStats());
  }

  getStats() {
    return {
      name: this.state.name,
      nodes: this.state.nodes.size,
      quads: this.state.quads.length,
      materials: new Set(this.state.quads.map(item => item.material)).size,
      layers: new Set(this.state.quads.map(item => item.layer)).size,
      modelPoints: this.state.modelPoints.length,
    };
  }

  setOptions(patch, { rebuild = true, preserveView = true } = {}) {
    this.options = { ...this.options, ...patch };
    if ('edges' in patch && this.state.edgeLines) this.state.edgeLines.visible = Boolean(this.options.edges);
    if ('nodes' in patch && this.state.nodePoints) this.state.nodePoints.visible = Boolean(this.options.nodes);
    if ('modelPoints' in patch && this.state.modelPointPoints) this.state.modelPointPoints.visible = Boolean(this.options.modelPoints);
    if ('opacity' in patch && this.state.mesh) {
      this.state.mesh.material.opacity = Number(this.options.opacity);
      this.state.mesh.material.transparent = Number(this.options.opacity) < 1;
      this.state.mesh.material.depthWrite = Number(this.options.opacity) >= 0.99;
      this.state.mesh.material.needsUpdate = true;
    }
    const geometryKeys = ['extrude', 'thicknessScale', 'colorBy'];
    if (rebuild && geometryKeys.some(key => key in patch) && this.state.quads.length) this.rebuild({ preserveView });
  }

  setView(kind = 'iso') {
    if (this.state.bounds.isEmpty()) return;
    const center = this.state.bounds.getCenter(new THREE.Vector3());
    const distance = this._fitDistance();
    const directions = {
      front: new THREE.Vector3(0, -1, 0),
      back: new THREE.Vector3(0, 1, 0),
      top: new THREE.Vector3(0, 0, 1),
      side: new THREE.Vector3(1, 0, 0),
      iso: new THREE.Vector3(1, -1, 0.72).normalize(),
    };
    const direction = directions[kind] ?? directions.iso;
    this.camera.position.copy(center).addScaledVector(direction, distance);
    this.camera.up.set(0, 0, 1);
    if (Math.abs(direction.z) > 0.999) this.camera.up.set(0, 1, 0);
    this.controls.target.copy(center);
    this.camera.near = Math.max(distance / 10000, 0.001);
    this.camera.far = Math.max(distance * 100, 1000);
    this.camera.updateProjectionMatrix();
    this.controls.update();
  }

  fit() { this.setView('iso'); }

  clearSelection() { this.selectQuad(null); }

  resize() {
    const width = Math.max(1, this.container.clientWidth);
    const height = Math.max(1, this.container.clientHeight);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  rebuild({ preserveView = true } = {}) {
    if (!this.state.quads.length) return;
    const target = this.controls.target.clone();
    const cameraPosition = this.camera.position.clone();
    const selected = this.state.selectedQuadIndex;

    this._disposeObject(this.state.mesh);
    this._disposeObject(this.state.edgeLines);
    this._disposeObject(this.state.nodePoints);
    this._disposeObject(this.state.modelPointPoints);
    this._disposeObject(this.state.selectionLine);
    this.state.mesh = this.state.edgeLines = this.state.nodePoints = this.state.modelPointPoints = this.state.selectionLine = null;

    const { geometry, edgeGeometry, triangleToQuad } = this._geometryForModel();
    const material = new THREE.MeshStandardMaterial({
      vertexColors: true,
      side: THREE.DoubleSide,
      roughness: 0.76,
      metalness: 0.02,
      transparent: Number(this.options.opacity) < 1,
      opacity: Number(this.options.opacity),
      depthWrite: Number(this.options.opacity) >= 0.99,
      polygonOffset: true,
      polygonOffsetFactor: 1,
      polygonOffsetUnits: 1,
    });
    this.state.mesh = new THREE.Mesh(geometry, material);
    this.state.mesh.name = 'Quads';
    this.modelGroup.add(this.state.mesh);
    this.state.triangleToQuad = triangleToQuad;

    this.state.edgeLines = new THREE.LineSegments(
      edgeGeometry,
      new THREE.LineBasicMaterial({ color: 0x0a0d12, transparent: true, opacity: 0.82 }),
    );
    this.state.edgeLines.visible = Boolean(this.options.edges);
    this.state.edgeLines.renderOrder = 2;
    this.modelGroup.add(this.state.edgeLines);

    const referencedNodes = [...new Set(this.state.quads.flatMap(quad => quad.nodeKeys))];
    const nodePositions = [];
    for (const key of referencedNodes) {
      const point = this.state.nodes.get(key);
      nodePositions.push(point.x, point.y, point.z);
    }
    const nodeGeometry = new THREE.BufferGeometry();
    nodeGeometry.setAttribute('position', new THREE.Float32BufferAttribute(nodePositions, 3));
    this.state.nodePoints = new THREE.Points(nodeGeometry, new THREE.PointsMaterial({ color: 0xffffff, size: 4, sizeAttenuation: false, depthTest: false }));
    this.state.nodePoints.visible = Boolean(this.options.nodes);
    this.state.nodePoints.renderOrder = 4;
    this.modelGroup.add(this.state.nodePoints);

    const pointPositions = [];
    for (const pointRecord of this.state.modelPoints) {
      const point = this.state.nodes.get(Number(pointRecord.nodeKey));
      if (point) pointPositions.push(point.x, point.y, point.z);
    }
    const mpGeometry = new THREE.BufferGeometry();
    mpGeometry.setAttribute('position', new THREE.Float32BufferAttribute(pointPositions, 3));
    this.state.modelPointPoints = new THREE.Points(mpGeometry, new THREE.PointsMaterial({ color: 0xffe66d, size: 8, sizeAttenuation: false, depthTest: false }));
    this.state.modelPointPoints.visible = Boolean(this.options.modelPoints);
    this.state.modelPointPoints.renderOrder = 6;
    this.modelGroup.add(this.state.modelPointPoints);

    this.state.bounds.copy(geometry.boundingBox ?? new THREE.Box3().setFromObject(this.state.mesh));
    this._updateGrid();
    this._emitLegend();
    if (selected != null) this.selectQuad(selected);
    if (!preserveView) this.setView('front');
    else {
      this.camera.position.copy(cameraPosition);
      this.controls.target.copy(target);
      this.controls.update();
    }
  }

  selectQuad(index) {
    this._disposeObject(this.state.selectionLine);
    this.state.selectionLine = null;
    if (index == null || !this.state.quads[index]) {
      this.state.selectedQuadIndex = null;
      this.emit('selection', null);
      return;
    }
    this.state.selectedQuadIndex = index;
    const quad = this.state.quads[index];
    const outline = this._selectedOutlinePoints(quad);
    const positions = [];
    for (const loop of outline.loops) {
      for (let item = 0; item < 4; item += 1) this._addSegment(positions, loop[item], loop[(item + 1) % 4]);
    }
    for (const [a, b] of outline.sides) this._addSegment(positions, a, b);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    this.state.selectionLine = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: 0xffff66, depthTest: false }));
    this.state.selectionLine.renderOrder = 10;
    this.modelGroup.add(this.state.selectionLine);
    this.emit('selection', { index, quad });
  }

  destroy() {
    this._resizeObserver.disconnect();
    this.renderer.dispose();
    this.container.removeChild(this.renderer.domElement);
  }

  _loadNormalized(model, modelPoints) {
    this.state.name = model.name;
    this.state.nodes = model.nodes;
    this.state.quads = model.quads;
    this.state.modelPoints = modelPoints ?? [];
    this.state.selectedQuadIndex = null;
    this.rebuild({ preserveView: false });
    this.clearSelection();
    this.emit('stats', this.getStats());
  }

  _categoryValue(quad) {
    switch (this.options.colorBy) {
      case 'material': return `Material ${quad.material}`;
      case 'layer': return `Layer ${quad.layer}`;
      case 'parent': return `${quad.parentType || 'Parent'} ${quad.parentKey}`;
      case 'thickness': return `Thickness ${mean(quad.thicknesses).toFixed(4)}`;
      case 'quad': return `Quad ${quad.key}`;
      case 'group': return quad.group || 'Component';
      case 'lane': return quad.lane || 'Lane';
      case 'role': return quad.transverseRole || 'Role';
      default: return 'Quads';
    }
  }

  _buildColorMap() {
    const values = [...new Set(this.state.quads.map(quad => this._categoryValue(quad)))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    const colors = new Map();
    if (this.options.colorBy === 'single') colors.set('Quads', new THREE.Color(0x6fb7ff));
    else {
      for (const value of values) {
        const hue = (hashString(value) % 360) / 360;
        colors.set(value, new THREE.Color().setHSL(hue, 0.62, 0.56));
      }
    }
    return colors;
  }

  _geometryForModel() {
    const data = { positions: [], colors: [], triangleToQuad: [], edgePositions: [] };
    const useExtrusion = Boolean(this.options.extrude) && Number(this.options.thicknessScale) > 0;
    this.state.categoryColors = this._buildColorMap();
    this.state.quads.forEach((quad, quadIndex) => {
      const base = quad.nodeKeys.map(key => this.state.nodes.get(key).clone());
      const color = this.state.categoryColors.get(this._categoryValue(quad));
      if (!useExtrusion) {
        this._pushQuadFace(data, base, color, quadIndex);
        for (let index = 0; index < 4; index += 1) this._addSegment(data.edgePositions, base[index], base[(index + 1) % 4]);
        return;
      }
      const normal = quad.normal.lengthSq() > 1e-18 ? quad.normal.clone().normalize() : computedNormal(base);
      const top = base.map((point, index) => point.clone().addScaledVector(normal, quad.thicknesses[index] * Number(this.options.thicknessScale) * 0.5));
      const bottom = base.map((point, index) => point.clone().addScaledVector(normal, -quad.thicknesses[index] * Number(this.options.thicknessScale) * 0.5));
      this._pushQuadFace(data, top, color, quadIndex);
      this._pushQuadFace(data, bottom, color, quadIndex, true);
      for (let index = 0; index < 4; index += 1) {
        const next = (index + 1) % 4;
        this._pushQuadFace(data, [top[index], top[next], bottom[next], bottom[index]], color, quadIndex);
        this._addSegment(data.edgePositions, top[index], top[next]);
        this._addSegment(data.edgePositions, bottom[index], bottom[next]);
        this._addSegment(data.edgePositions, top[index], bottom[index]);
      }
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(data.positions, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(data.colors, 3));
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
    const edgeGeometry = new THREE.BufferGeometry();
    edgeGeometry.setAttribute('position', new THREE.Float32BufferAttribute(data.edgePositions, 3));
    return { geometry, edgeGeometry, triangleToQuad: data.triangleToQuad };
  }

  _pushTriangle(target, a, b, c, color, quadIndex) {
    for (const point of [a, b, c]) {
      target.positions.push(point.x, point.y, point.z);
      target.colors.push(color.r, color.g, color.b);
    }
    target.triangleToQuad.push(quadIndex);
  }

  _pushQuadFace(target, points, color, quadIndex, reverse = false) {
    const values = reverse ? [...points].reverse() : points;
    this._pushTriangle(target, values[0], values[1], values[2], color, quadIndex);
    this._pushTriangle(target, values[0], values[2], values[3], color, quadIndex);
  }

  _addSegment(array, a, b) { array.push(a.x, a.y, a.z, b.x, b.y, b.z); }

  _selectedOutlinePoints(quad) {
    const base = quad.nodeKeys.map(key => this.state.nodes.get(key).clone());
    if (!this.options.extrude || Number(this.options.thicknessScale) <= 0) return { loops: [base], sides: [] };
    const normal = quad.normal.lengthSq() > 1e-18 ? quad.normal.clone().normalize() : computedNormal(base);
    const top = base.map((point, index) => point.clone().addScaledVector(normal, quad.thicknesses[index] * Number(this.options.thicknessScale) * 0.5));
    const bottom = base.map((point, index) => point.clone().addScaledVector(normal, -quad.thicknesses[index] * Number(this.options.thicknessScale) * 0.5));
    return { loops: [top, bottom], sides: top.map((point, index) => [point, bottom[index]]) };
  }

  _disposeObject(object) {
    if (!object) return;
    this.modelGroup.remove(object);
    object.geometry?.dispose();
    if (Array.isArray(object.material)) object.material.forEach(material => material.dispose());
    else object.material?.dispose();
  }

  _updateGrid() {
    if (this.state.bounds.isEmpty()) return;
    const size = this.state.bounds.getSize(new THREE.Vector3());
    const center = this.state.bounds.getCenter(new THREE.Vector3());
    const maxSize = Math.max(size.x, size.y, size.z, 1);
    this.grid.scale.setScalar(maxSize / 1200);
    this.grid.position.set(center.x, center.y, Math.min(this.state.bounds.min.z, 0));
    this.axes.scale.setScalar(Math.max(maxSize / 350, 0.25));
    this.axes.position.copy(center);
  }

  _fitDistance() {
    const size = this.state.bounds.getSize(new THREE.Vector3());
    const radius = Math.max(size.length() * 0.5, 1);
    const verticalFov = THREE.MathUtils.degToRad(this.camera.fov);
    const verticalDistance = radius / Math.sin(verticalFov * 0.5);
    const horizontalFov = 2 * Math.atan(Math.tan(verticalFov * 0.5) * this.camera.aspect);
    const horizontalDistance = radius / Math.sin(horizontalFov * 0.5);
    return Math.max(verticalDistance, horizontalDistance) * 1.08;
  }

  _emitLegend() {
    this.emit('legend', [...this.state.categoryColors.entries()].map(([label, color]) => ({ label, color: `#${color.getHexString()}` })));
  }

  _onPointerDown(event) {
    if (event.button !== 0) return;
    this._pointerStart = { x: event.clientX, y: event.clientY };
  }

  _onPointerUp(event) {
    if (!this.state.mesh || event.button !== 0 || !this._pointerStart) return;
    const moved = Math.hypot(event.clientX - this._pointerStart.x, event.clientY - this._pointerStart.y);
    this._pointerStart = null;
    if (moved > 5) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hit = this.raycaster.intersectObject(this.state.mesh, false)[0];
    if (!hit || hit.faceIndex == null) return;
    const quadIndex = this.state.triangleToQuad[hit.faceIndex];
    if (quadIndex != null) this.selectQuad(quadIndex);
  }

  _animate() {
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
    requestAnimationFrame(() => this._animate());
  }
}
