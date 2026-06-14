import { useState, useEffect } from 'react';
import { getBlobUrl } from '../api';
import { useTranslation } from '../i18n';

export interface StructureImageProps {
  structureId: string;
  token: string;
}

export function StructureImage({ structureId, token }: StructureImageProps) {
  const { t } = useTranslation();
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [error, setError] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    let active = true;
    let urlToRevoke: string | null = null;

    setLoading(true);
    setError(false);

    getBlobUrl(`/api/rdf/structures/${structureId}/image.svg`, token)
      .then((url) => {
        if (active) {
          urlToRevoke = url;
          setBlobUrl(url);
          setLoading(false);
        } else {
          URL.revokeObjectURL(url);
        }
      })
      .catch((err) => {
        console.error('Failed to load structure image:', err);
        if (active) {
          setError(true);
          setLoading(false);
        }
      });

    return () => {
      active = false;
      if (urlToRevoke) {
        URL.revokeObjectURL(urlToRevoke);
      }
    };
  }, [structureId, token]);

  if (loading) {
    return <span className="muted" style={{ fontSize: '12px' }}>{t('加载中...')}</span>;
  }
  if (error || !blobUrl) {
    return <span className="text-danger" style={{ fontSize: '12px', color: 'var(--color-danger, #ef4444)' }}>{t('加载失败')}</span>;
  }

  return (
    <img
      src={blobUrl}
      alt={t('结构图')}
      style={{ maxWidth: '150px', maxHeight: '100px', backgroundColor: 'white', border: '1px solid var(--color-border, #e2e8f0)', borderRadius: '4px' }}
    />
  );
}
