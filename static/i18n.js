/**
 * 프론트엔드 공용 i18n 번역 및 로케일 포맷 헬퍼.
 * 각 HTML 템플릿의 <head>에서 주입한 window.I18N / window.I18N_LOCALE을 사용한다.
 */
(function (global) {
  'use strict';

  function lookupKey(dict, key) {
    if (!dict || typeof key !== 'string') {
      return undefined;
    }
    var parts = key.split('.');
    var cur = dict;
    for (var i = 0; i < parts.length; i += 1) {
      if (cur === null || typeof cur !== 'object' || !(parts[i] in cur)) {
        return undefined;
      }
      cur = cur[parts[i]];
    }
    return typeof cur === 'string' ? cur : undefined;
  }

  function formatParams(text, params) {
    if (!params || typeof params !== 'object') {
      return text;
    }
    return text.replace(/\{([a-zA-Z0-9_]+)\}/g, function (match, name) {
      if (Object.prototype.hasOwnProperty.call(params, name) && params[name] !== undefined && params[name] !== null) {
        return String(params[name]);
      }
      return match;
    });
  }

  global.t = function (key, params) {
    var bundle = global.I18N || {};
    var found = lookupKey(bundle, key);
    var text = found !== undefined ? found : key;
    return formatParams(text, params);
  };

  function resolveLocale() {
    return global.I18N_LOCALE || undefined;
  }

  function toDateObject(value) {
    if (value instanceof Date) {
      return value;
    }
    if (typeof value === 'number') {
      return new Date(value);
    }
    return new Date(value);
  }

  global.formatLocaleDateTime = function (value, options) {
    var d = toDateObject(value);
    if (Number.isNaN(d.getTime())) {
      return String(value || '');
    }
    return d.toLocaleString(resolveLocale(), options);
  };

  global.formatLocaleTime = function (value, options) {
    var d = toDateObject(value);
    if (Number.isNaN(d.getTime())) {
      return String(value || '');
    }
    return d.toLocaleTimeString(resolveLocale(), options);
  };

  global.formatLocaleDate = function (value, options) {
    var d = toDateObject(value);
    if (Number.isNaN(d.getTime())) {
      return String(value || '');
    }
    return d.toLocaleDateString(resolveLocale(), options);
  };

  global.formatLocaleNumber = function (value, options) {
    var num = Number(value);
    if (Number.isNaN(num)) {
      return String(value || '');
    }
    return num.toLocaleString(resolveLocale(), options);
  };
})(window);
