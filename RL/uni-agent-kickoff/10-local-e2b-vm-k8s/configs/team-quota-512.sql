BEGIN;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.teams WHERE id = '0b8a3ded-4489-4722-afd1-1d82e64ec2d5'::uuid) THEN
        RAISE EXCEPTION 'Target team does not exist; replace the deployment-specific team UUID before applying';
    END IF;
END $$;
SELECT id FROM public.teams
WHERE id = '0b8a3ded-4489-4722-afd1-1d82e64ec2d5'::uuid FOR UPDATE;

INSERT INTO public.project_limits (
    team_id, max_length_hours, concurrent_sandboxes, concurrent_template_builds,
    max_vcpu, max_ram_mb, disk_mb, events_ttl_days, default_free_disk_size_mb,
    max_disk_size_mb, max_free_disk_size_mb, updated_at
)
SELECT id, max_length_hours, 512, concurrent_template_builds,
    max_vcpu, max_ram_mb, disk_mb, events_ttl_days, default_free_disk_size_mb,
    max_disk_size_mb, max_free_disk_size_mb, now()
FROM public.team_limits
WHERE id = '0b8a3ded-4489-4722-afd1-1d82e64ec2d5'::uuid
ON CONFLICT (team_id) DO UPDATE
SET concurrent_sandboxes = EXCLUDED.concurrent_sandboxes, updated_at = now();

SELECT json_build_object('concurrent_sandboxes', concurrent_sandboxes,
    'max_ram_mb', max_ram_mb, 'max_vcpu', max_vcpu)
FROM public.team_limits
WHERE id = '0b8a3ded-4489-4722-afd1-1d82e64ec2d5'::uuid;
COMMIT;
